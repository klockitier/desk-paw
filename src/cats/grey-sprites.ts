import {
  ANIMATIONS,
  FRAME_HEIGHT,
  FRAME_WIDTH,
  type AnimationName,
} from "../assets/grey/manifest";

export { FRAME_HEIGHT, FRAME_WIDTH };
export type { AnimationName };

export type Facing = "front" | "left" | "right";

const urls = import.meta.glob("../assets/grey/*.png", {
  eager: true,
  query: "?url",
  import: "default",
}) as Record<string, string>;

const urlByFile = new Map<string, string>();
for (const [path, url] of Object.entries(urls)) {
  urlByFile.set(path.slice(path.lastIndexOf("/") + 1), url);
}

const loaded = new Map<string, HTMLImageElement[]>();

function loadImage(file: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`missing grey sprite ${file}`));
    img.src = urlByFile.get(file) ?? "";
  });
}

export async function preload(): Promise<void> {
  await Promise.all(
    (Object.keys(ANIMATIONS) as AnimationName[]).map(async (name) => {
      const files = ANIMATIONS[name] as readonly string[];
      const results = await Promise.allSettled(files.map(loadImage));
      const ok = results.flatMap((r) => (r.status === "fulfilled" ? [r.value] : []));
      if (ok.length !== files.length) {
        console.warn(`grey sprites: ${name} loaded ${ok.length}/${files.length} frames`);
      }
      loaded.set(name, ok);
    }),
  );
}

export function framesOf(name: string): HTMLImageElement[] {
  const frames = loaded.get(name);
  if (frames?.length) return frames;
  return loaded.get("idle_right") ?? loaded.get("idle_front") ?? [];
}

/** Grey sheets are side-view only; prefer side, then front copy, then idle. */
export function resolveAnimation(base: string, facing: Facing): string {
  const candidates = [`${base}_${facing}`, `${base}_right`, `${base}_left`, "idle_right", "idle_front"];
  for (const key of candidates) {
    if (key in ANIMATIONS) return key;
  }
  return "idle_right";
}
