import {
  ANIMATIONS,
  FRAME_HEIGHT,
  FRAME_WIDTH,
  type AnimationName,
} from "../assets/cat/manifest";

export { FRAME_HEIGHT, FRAME_WIDTH };
export type { AnimationName };

export type Facing = "front" | "left" | "right";

const urls = import.meta.glob("../assets/cat/*.png", {
  eager: true,
  query: "?url",
  import: "default",
}) as Record<string, string>;

const urlByFile = new Map<string, string>();
for (const [path, url] of Object.entries(urls)) {
  urlByFile.set(path.slice(path.lastIndexOf("/") + 1), url);
}

const loaded = new Map<AnimationName, HTMLImageElement[]>();

function loadImage(file: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`missing sprite ${file}`));
    img.src = urlByFile.get(file) ?? "";
  });
}

export async function preload(): Promise<void> {
  await Promise.all(
    (Object.keys(ANIMATIONS) as AnimationName[]).map(async (name) => {
      const files = ANIMATIONS[name] as readonly string[];
      // Per-frame, not all-or-nothing: one missing PNG used to reject the whole
      // preload, so the cat never became ready and the window stayed empty.
      const results = await Promise.allSettled(files.map(loadImage));
      const ok = results.flatMap((r) => (r.status === "fulfilled" ? [r.value] : []));
      if (ok.length !== files.length) {
        console.warn(`sprites: ${name} loaded ${ok.length}/${files.length} frames`);
      }
      loaded.set(name, ok);
    }),
  );
}

/** Frames for an animation, falling back to idle so the cat is never invisible. */
export function framesOf(name: AnimationName): HTMLImageElement[] {
  const frames = loaded.get(name);
  if (frames?.length) return frames;
  return loaded.get("idle_front") ?? [];
}

/** Pick `<base>_<facing>`, falling back to the front view then to idle. */
export function resolveAnimation(base: string, facing: Facing): AnimationName {
  const candidates = [`${base}_${facing}`, `${base}_front`, "idle_front"];
  for (const key of candidates) {
    if (key in ANIMATIONS) return key as AnimationName;
  }
  return "idle_front";
}
