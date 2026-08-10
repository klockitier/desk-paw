use serde::Serialize;
use std::io::{BufRead, BufReader, Write};
use std::net::TcpListener;
use std::sync::atomic::{AtomicBool, AtomicPtr, AtomicU32, Ordering};
use std::thread;
use std::time::Duration;
use tauri::{AppHandle, Emitter, LogicalPosition, Manager, WebviewUrl, WebviewWindowBuilder};
use tauri::utils::config::BackgroundThrottlingPolicy;

const PAWCTL_PORT: u16 = 19283;
const WINDOW_SIZE: f64 = 180.0;

/// Pause auto-follow while the user is dragging the cat.
static FOLLOW_PAUSED: AtomicBool = AtomicBool::new(false);
/// Warm-up delay so restore/clamp runs before follow nudges the window off-screen.
static FOLLOW_ENABLED: AtomicBool = AtomicBool::new(false);
/// Menu open / explicit interact — don't click-through while true.
static INTERACTIVE_LOCKED: AtomicBool = AtomicBool::new(false);
static LAST_CLICK_THROUGH: AtomicBool = AtomicBool::new(false);
/// Walker cat moves itself in the frontend; classic uses smooth follow here.
static WALKER_MODE: AtomicBool = AtomicBool::new(true);
/// Log the first key event once, so `tauri dev` shows whether the tap really fires.
static KEY_SEEN: AtomicBool = AtomicBool::new(false);
/// Sprite canvas size, reported by the frontend from the generated manifest, so the
/// click-through hit box follows the artwork instead of a constant that drifts.
static CAT_W: AtomicU32 = AtomicU32::new(142);
static CAT_H: AtomicU32 = AtomicU32::new(142);

#[derive(Clone, Serialize)]
struct MousePos {
    x: f64,
    y: f64,
    /// Physical left button state — the frontend ends a drag on the real release,
    /// since `startDragging()` resolves as soon as the drag *starts*.
    down: bool,
}

#[derive(Clone, Serialize)]
struct AgentEvent {
    event: String,
}

#[derive(Clone, Serialize)]
struct PermissionStatus {
    keyboard: bool,
    message: String,
}

#[tauri::command]
fn quit_app(app: AppHandle) {
    app.exit(0);
}

#[tauri::command]
fn get_pawctl_port() -> u16 {
    PAWCTL_PORT
}

#[tauri::command]
fn set_follow_paused(paused: bool) {
    FOLLOW_PAUSED.store(paused, Ordering::SeqCst);
}

#[tauri::command]
fn set_interactive_locked(app: AppHandle, locked: bool) {
    INTERACTIVE_LOCKED.store(locked, Ordering::SeqCst);
    if locked {
        apply_click_through(&app, false);
    }
}

#[tauri::command]
fn set_walker_mode(walker: bool) {
    WALKER_MODE.store(walker, Ordering::SeqCst);
}

#[tauri::command]
fn set_cat_size(w: u32, h: u32) {
    CAT_W.store(w.max(1), Ordering::SeqCst);
    CAT_H.store(h.max(1), Ordering::SeqCst);
}

#[tauri::command]
fn reset_position(app: AppHandle) -> Result<(), String> {
    center_window(&app).map_err(|e| e.to_string())
}

fn center_window(app: &AppHandle) -> Result<(), tauri::Error> {
    let Some(win) = app.get_webview_window("main") else {
        return Ok(());
    };
    if let Ok(Some(monitor)) = win.current_monitor() {
        let size = monitor.size();
        let scale = monitor.scale_factor();
        let mw = size.width as f64 / scale;
        let mh = size.height as f64 / scale;
        let x = (mw - WINDOW_SIZE) / 2.0;
        let y = (mh - WINDOW_SIZE) / 2.0;
        win.set_position(LogicalPosition::new(x, y))?;
    }
    let _ = win.show();
    let _ = win.set_always_on_top(true);
    Ok(())
}

fn clamp_logical(app: &AppHandle, x: f64, y: f64, w: f64, h: f64) -> (f64, f64) {
    let Some(win) = app.get_webview_window("main") else {
        return (x, y);
    };
    let Ok(Some(monitor)) = win.current_monitor() else {
        return (x, y);
    };
    let size = monitor.size();
    let scale = monitor.scale_factor();
    let pos = monitor.position();
    let mx = pos.x as f64 / scale;
    let my = pos.y as f64 / scale;
    let mw = size.width as f64 / scale;
    let mh = size.height as f64 / scale;
    (
        x.clamp(mx, (mx + mw - w).max(mx)),
        y.clamp(my, (my + mh - h).max(my)),
    )
}

fn apply_click_through(app: &AppHandle, ignore: bool) {
    if LAST_CLICK_THROUGH.load(Ordering::SeqCst) == ignore {
        return;
    }
    if let Some(win) = app.get_webview_window("main") {
        if win.set_ignore_cursor_events(ignore).is_ok() {
            LAST_CLICK_THROUGH.store(ignore, Ordering::SeqCst);
        }
    }
}

/// True when the cursor is over the visible cat sprite (not transparent window padding).
fn mouse_over_cat(app: &AppHandle, mouse_x: f64, mouse_y: f64) -> bool {
    let Some(win) = app.get_webview_window("main") else {
        return false;
    };
    let Ok(pos) = win.outer_position() else {
        return false;
    };
    let Ok(size) = win.outer_size() else {
        return false;
    };
    let Ok(scale) = win.scale_factor() else {
        return false;
    };

    let wx = pos.x as f64 / scale;
    let wy = pos.y as f64 / scale;
    let ww = size.width as f64 / scale;
    let wh = size.height as f64 / scale;

    let (cat_w, cat_h) = if WALKER_MODE.load(Ordering::SeqCst) {
        (
            CAT_W.load(Ordering::SeqCst) as f64,
            CAT_H.load(Ordering::SeqCst) as f64,
        )
    } else {
        (112.0, 112.0)
    };
    let pad_x = (ww - cat_w) / 2.0;
    let pad_y = (wh - cat_h) / 2.0;

    mouse_x >= wx + pad_x
        && mouse_x <= wx + pad_x + cat_w
        && mouse_y >= wy + pad_y
        && mouse_y <= wy + pad_y + cat_h
}

/// Physical left mouse button state, independent of which window has focus.
#[cfg(target_os = "macos")]
fn left_button_down() -> bool {
    #[link(name = "CoreGraphics", kind = "framework")]
    extern "C" {
        fn CGEventSourceButtonState(state_id: i32, button: u32) -> bool;
    }
    // kCGEventSourceStateCombinedSessionState, kCGMouseButtonLeft
    unsafe { CGEventSourceButtonState(0, 0) }
}

fn update_click_through(app: &AppHandle, mouse_x: f64, mouse_y: f64) {
    if INTERACTIVE_LOCKED.load(Ordering::SeqCst) || FOLLOW_PAUSED.load(Ordering::SeqCst) {
        apply_click_through(app, false);
        return;
    }
    // Never turn on click-through mid-drag: the cursor drifts off the sprite onto
    // transparent padding constantly while dragging, and that aborts the drag.
    #[cfg(target_os = "macos")]
    if left_button_down() && !LAST_CLICK_THROUGH.load(Ordering::SeqCst) {
        return;
    }
    let over_cat = mouse_over_cat(app, mouse_x, mouse_y);
    apply_click_through(app, !over_cat);
}

#[cfg(target_os = "macos")]
fn make_webview_transparent(win: &tauri::WebviewWindow) {
    use objc::runtime::{Class, Object, NO};
    use objc::{msg_send, sel, sel_impl};

    if let Ok(ptr) = win.ns_window() {
        unsafe {
            let ns_window = ptr as *mut Object;
            let clear: *mut Object = msg_send![Class::get("NSColor").unwrap(), clearColor];
            let _: () = msg_send![ns_window, setOpaque: NO];
            let _: () = msg_send![ns_window, setBackgroundColor: clear];
            let _: () = msg_send![ns_window, setHasShadow: NO];

            // CanJoinAllSpaces | Stationary | FullScreenAuxiliary
            let existing: usize = msg_send![ns_window, collectionBehavior];
            let _: () = msg_send![ns_window, setCollectionBehavior: existing | 1 | 16 | 256];
        }
    }
}

fn start_pawctl_server(app: AppHandle) {
    thread::spawn(move || {
        let addr = format!("127.0.0.1:{PAWCTL_PORT}");
        let listener = match TcpListener::bind(&addr) {
            Ok(l) => l,
            Err(e) => {
                eprintln!("pawctl server bind failed on {addr}: {e}");
                return;
            }
        };
        println!("pawctl listening on {addr}");

        for stream in listener.incoming().flatten() {
            let mut reader = BufReader::new(&stream);
            let mut line = String::new();
            if reader.read_line(&mut line).is_err() {
                continue;
            }
            let cmd = line.trim().to_lowercase();
            if cmd.is_empty() {
                continue;
            }
            let allowed = [
                "working", "waiting", "done", "error", "overheated", "idle", "happy", "sleeping",
            ];
            if allowed.contains(&cmd.as_str()) {
                let _ = app.emit("agent-event", AgentEvent { event: cmd.clone() });
                let mut stream = stream;
                let _ = writeln!(stream, "ok {cmd}");
            } else {
                let mut stream = stream;
                let _ = writeln!(stream, "err unknown:{cmd}");
            }
        }
    });
}

#[cfg(target_os = "macos")]
fn ax_trusted() -> bool {
    #[link(name = "ApplicationServices", kind = "framework")]
    extern "C" {
        fn AXIsProcessTrusted() -> u8;
    }
    unsafe { AXIsProcessTrusted() != 0 }
}

/// Same check, but lets macOS show its own Accessibility dialog.
///
/// Worth the FFI: the system adds the *correct* process to the Accessibility list
/// itself. Under `tauri dev` that is whatever launched the binary (terminal or
/// editor), which is near-impossible to pick correctly by hand.
#[cfg(target_os = "macos")]
fn ax_trusted_prompting() -> bool {
    use std::ffi::c_void;
    use std::ptr;

    type CFTypeRef = *const c_void;

    #[link(name = "ApplicationServices", kind = "framework")]
    #[link(name = "CoreFoundation", kind = "framework")]
    extern "C" {
        static kAXTrustedCheckOptionPrompt: CFTypeRef;
        static kCFBooleanTrue: CFTypeRef;
        static kCFTypeDictionaryKeyCallBacks: c_void;
        static kCFTypeDictionaryValueCallBacks: c_void;
        fn CFDictionaryCreate(
            allocator: *const c_void,
            keys: *const CFTypeRef,
            values: *const CFTypeRef,
            count: isize,
            key_callbacks: *const c_void,
            value_callbacks: *const c_void,
        ) -> *const c_void;
        fn CFRelease(cf: CFTypeRef);
        fn AXIsProcessTrustedWithOptions(options: *const c_void) -> u8;
    }

    unsafe {
        let keys = [kAXTrustedCheckOptionPrompt];
        let values = [kCFBooleanTrue];
        let options = CFDictionaryCreate(
            ptr::null(),
            keys.as_ptr(),
            values.as_ptr(),
            1,
            &kCFTypeDictionaryKeyCallBacks as *const c_void,
            &kCFTypeDictionaryValueCallBacks as *const c_void,
        );
        let trusted = AXIsProcessTrustedWithOptions(options) != 0;
        if !options.is_null() {
            CFRelease(options);
        }
        trusted
    }
}

/// Global mouse position (top-left screen coords, matches Tauri window positions).
#[cfg(target_os = "macos")]
fn global_mouse_pos() -> Option<(f64, f64)> {
    use std::ffi::c_void;
    use std::ptr;

    #[repr(C)]
    struct CGPoint {
        x: f64,
        y: f64,
    }

    #[link(name = "CoreGraphics", kind = "framework")]
    #[link(name = "CoreFoundation", kind = "framework")]
    extern "C" {
        fn CGEventCreate(source: *const c_void) -> *mut c_void;
        fn CGEventGetLocation(event: *mut c_void) -> CGPoint;
        fn CFRelease(cf: *const c_void);
    }

    unsafe {
        let event = CGEventCreate(ptr::null());
        if event.is_null() {
            return None;
        }
        let loc = CGEventGetLocation(event);
        CFRelease(event);
        Some((loc.x, loc.y))
    }
}

/// Ease the pet window toward the cursor; stop at a comfortable follow distance.
#[cfg(target_os = "macos")]
fn follow_cursor(app: &AppHandle, mouse_x: f64, mouse_y: f64) {
    if WALKER_MODE.load(Ordering::SeqCst) {
        return;
    }
    if !FOLLOW_ENABLED.load(Ordering::SeqCst) || FOLLOW_PAUSED.load(Ordering::SeqCst) {
        return;
    }
    let Some(win) = app.get_webview_window("main") else {
        return;
    };
    let Ok(pos) = win.outer_position() else {
        return;
    };
    let Ok(size) = win.outer_size() else {
        return;
    };
    let Ok(scale) = win.scale_factor() else {
        return;
    };

    let win_w = size.width as f64 / scale;
    let win_h = size.height as f64 / scale;
    let cx = pos.x as f64 / scale + win_w / 2.0;
    let cy = pos.y as f64 / scale + win_h / 2.0;

    let dx = mouse_x - cx;
    let dy = mouse_y - cy;
    let dist = (dx * dx + dy * dy).sqrt();
    if dist < 1.0 {
        return;
    }

    // Keep a gap so the cat trails near the cursor instead of sitting on it.
    const STOP_DIST: f64 = 64.0;
    const LERP: f64 = 0.18;
    if dist <= STOP_DIST {
        return;
    }

    let move_by = (dist - STOP_DIST) * LERP;
    let nx = cx + (dx / dist) * move_by;
    let ny = cy + (dy / dist) * move_by;
    let (px, py) = clamp_logical(app, nx - win_w / 2.0, ny - win_h / 2.0, win_w, win_h);
    let _ = win.set_position(LogicalPosition::new(px, py));
}

#[cfg(target_os = "macos")]
fn start_mouse_poll(app: AppHandle) {
    thread::spawn(move || {
        loop {
            if let Some((x, y)) = global_mouse_pos() {
                update_click_through(&app, x, y);
                follow_cursor(&app, x, y);
                // Always emit so eyes keep updating while the window moves / app is unfocused.
                let _ = app.emit(
                    "mouse-global",
                    MousePos {
                        x,
                        y,
                        down: left_button_down(),
                    },
                );
            }
            thread::sleep(Duration::from_millis(16));
        }
    });
}

/// Key-activity-only tap. Does not read or convert key characters (avoids rdev/HIToolbox crash).
#[cfg(target_os = "macos")]
fn start_key_activity_tap(app: AppHandle) {
    use std::ffi::c_void;
    use std::ptr;

    type CFMachPortRef = *mut c_void;
    type CFRunLoopSourceRef = *mut c_void;
    type CFRunLoopRef = *mut c_void;
    type CGEventTapProxy = *mut c_void;
    type CGEventRef = *mut c_void;

    type CGEventTapCallBack = Option<
        unsafe extern "C" fn(
            CGEventTapProxy,
            u32,
            CGEventRef,
            *mut c_void,
        ) -> CGEventRef,
    >;

    #[link(name = "CoreGraphics", kind = "framework")]
    #[link(name = "CoreFoundation", kind = "framework")]
    extern "C" {
        fn CGEventTapCreate(
            tap: u32,
            place: u32,
            options: u32,
            events_of_interest: u64,
            callback: CGEventTapCallBack,
            user_info: *mut c_void,
        ) -> CFMachPortRef;
        fn CGEventTapEnable(tap: CFMachPortRef, enable: bool);
        fn CFMachPortCreateRunLoopSource(
            allocator: *const c_void,
            port: CFMachPortRef,
            order: i64,
        ) -> CFRunLoopSourceRef;
        fn CFRunLoopGetCurrent() -> CFRunLoopRef;
        fn CFRunLoopAddSource(rl: CFRunLoopRef, source: CFRunLoopSourceRef, mode: *const c_void);
        fn CFRunLoopRun();
        static kCFRunLoopCommonModes: *const c_void;
    }

    const KCG_HID_EVENT_TAP: u32 = 0;
    const KCG_HEAD_INSERT: u32 = 0;
    /// Active tap. Listen-only taps are gated by Input Monitoring on macOS, while
    /// this one works with the Accessibility grant the app already asks for.
    const KCG_TAP_DEFAULT: u32 = 0;
    const KCG_EVENT_KEY_DOWN: u32 = 10;
    const KCG_EVENT_KEY_UP: u32 = 11;
    const KCG_TAP_DISABLED_BY_TIMEOUT: u32 = 0xFFFF_FFFE;
    const KCG_TAP_DISABLED_BY_USER_INPUT: u32 = 0xFFFF_FFFF;

    struct TapCtx {
        app: AppHandle,
        tap: AtomicPtr<c_void>,
    }

    unsafe extern "C" fn callback(
        _proxy: CGEventTapProxy,
        event_type: u32,
        event: CGEventRef,
        user: *mut c_void,
    ) -> CGEventRef {
        let ctx = &*(user as *const TapCtx);
        match event_type {
            KCG_EVENT_KEY_DOWN | KCG_EVENT_KEY_UP => {
                if !KEY_SEEN.swap(true, Ordering::SeqCst) {
                    println!("[desk-paw] first key event seen — typing detection is live");
                }
                let _ = ctx.app.emit("key-activity", ());
            }
            // The system can switch a tap off; typing silently stopped registering
            // until the app restarted. Turn it back on instead.
            KCG_TAP_DISABLED_BY_TIMEOUT | KCG_TAP_DISABLED_BY_USER_INPUT => {
                println!("[desk-paw] key tap disabled by system — re-enabling");
                let tap = ctx.tap.load(Ordering::SeqCst);
                if !tap.is_null() {
                    CGEventTapEnable(tap, true);
                }
            }
            _ => {}
        }
        event
    }

    thread::spawn(move || {
        // Permission is usually granted *after* first launch; keep waiting for it
        // rather than needing a restart.
        let mut warned = false;
        // First check prompts, so macOS registers the right process itself.
        while !(if warned { ax_trusted() } else { ax_trusted_prompting() }) {
            if !warned {
                warned = true;
                println!(
                    "[desk-paw] AXIsProcessTrusted() = false — waiting for Accessibility. \
                     Grant it to whichever app owns this process (in `tauri dev` that is \
                     usually your terminal/editor, not Desk Paw)."
                );
                let _ = app.emit(
                    "permission-status",
                    PermissionStatus {
                        keyboard: false,
                        message: "Enable Accessibility for Desk Paw to detect typing in other apps (System Settings → Privacy & Security → Accessibility). Local mouse/click still works.".into(),
                    },
                );
            }
            thread::sleep(Duration::from_secs(3));
        }

        let ctx = Box::new(TapCtx {
            app: app.clone(),
            tap: AtomicPtr::new(ptr::null_mut()),
        });
        let user = Box::into_raw(ctx) as *mut c_void;
        let mask: u64 = (1 << KCG_EVENT_KEY_DOWN) | (1 << KCG_EVENT_KEY_UP);

        unsafe {
            let tap = CGEventTapCreate(
                KCG_HID_EVENT_TAP,
                KCG_HEAD_INSERT,
                KCG_TAP_DEFAULT,
                mask,
                Some(callback),
                user,
            );
            if tap.is_null() {
                let _ = app.emit(
                    "permission-status",
                    PermissionStatus {
                        keyboard: false,
                        message: "Could not create keyboard event tap. Grant Accessibility (and Input Monitoring if listed), then restart.".into(),
                    },
                );
                let _ = Box::from_raw(user as *mut TapCtx);
                return;
            }
            (*(user as *mut TapCtx)).tap.store(tap, Ordering::SeqCst);

            println!("[desk-paw] key tap created; waiting for key events");
            let source = CFMachPortCreateRunLoopSource(ptr::null(), tap, 0);
            CFRunLoopAddSource(CFRunLoopGetCurrent(), source, kCFRunLoopCommonModes);
            CGEventTapEnable(tap, true);
            println!("key-activity tap active");
            CFRunLoopRun();
        }
    });
}

#[cfg(target_os = "macos")]
fn start_input_monitor(app: AppHandle) {
    start_mouse_poll(app.clone());
    start_key_activity_tap(app);
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            quit_app,
            get_pawctl_port,
            set_follow_paused,
            set_interactive_locked,
            set_walker_mode,
            set_cat_size,
            reset_position
        ])
        .setup(|app| {
            #[cfg(target_os = "macos")]
            {
                app.set_activation_policy(tauri::ActivationPolicy::Accessory);
            }

            let win = WebviewWindowBuilder::new(app, "main", WebviewUrl::default())
                .title("Desk Paw")
                .inner_size(WINDOW_SIZE, WINDOW_SIZE)
                .resizable(false)
                .decorations(false)
                .transparent(true)
                .always_on_top(true)
                .skip_taskbar(true)
                .visible(true)
                .focused(true)
                .accept_first_mouse(true)
                .background_throttling(BackgroundThrottlingPolicy::Disabled)
                .build()?;

            #[cfg(target_os = "macos")]
            make_webview_transparent(&win);

            let _ = center_window(app.handle());
            let _ = win.show();
            let _ = win.set_always_on_top(true);
            apply_click_through(app.handle(), true);

            let app_follow = app.handle().clone();
            thread::spawn(move || {
                thread::sleep(Duration::from_millis(1200));
                FOLLOW_ENABLED.store(true, Ordering::SeqCst);
                if let Some((mx, my)) = global_mouse_pos() {
                    follow_cursor(&app_follow, mx, my);
                }
            });

            start_pawctl_server(app.handle().clone());

            #[cfg(target_os = "macos")]
            start_input_monitor(app.handle().clone());

            #[cfg(not(target_os = "macos"))]
            {
                let _ = app.handle().emit(
                    "permission-status",
                    PermissionStatus {
                        keyboard: false,
                        message: "Global input monitoring is only implemented for macOS in this MVP.".into(),
                    },
                );
            }

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
