import type { PetState } from "../state";
import type { CatController } from "./types";

export class ClassicCat implements CatController {
  kind = "classic" as const;
  private cat!: HTMLElement;
  private pupils: HTMLElement[] = [];

  mount(root: HTMLElement): void {
    this.cat = root.querySelector("#cat-classic") as HTMLElement;
    this.pupils = Array.from(this.cat.querySelectorAll<HTMLElement>(".pupil"));
  }

  show(): void {
    this.cat.classList.remove("hidden");
  }

  hide(): void {
    this.cat.classList.add("hidden");
  }

  setState(state: PetState): void {
    this.cat.className = `cat ${`state-${state.toLowerCase()}`}`;
  }

  onCursor(
    screenX: number,
    screenY: number,
    winX: number,
    winY: number,
    winW: number,
    winH: number,
  ): number {
    const cx = winX + winW / 2;
    const cy = winY + winH / 2;
    const dx = screenX - cx;
    const dy = screenY - cy;
    const max = 3.5;
    const dist = Math.hypot(dx, dy) || 1;
    const ox = (dx / dist) * Math.min(max, dist / 36);
    const oy = (dy / dist) * Math.min(max, dist / 36);
    for (const p of this.pupils) {
      p.style.transform = `translate(${ox}px, ${oy}px)`;
    }
    return Math.hypot(screenX - cx, screenY - cy);
  }

  onLocalPointer(offsetX: number, offsetY: number): number {
    const max = 3.5;
    const dx = offsetX - 56;
    const dy = offsetY - 56;
    const len = Math.hypot(dx, dy) || 1;
    for (const p of this.pupils) {
      p.style.transform = `translate(${(dx / len) * max}px, ${(dy / len) * max}px)`;
    }
    return Math.hypot(dx, dy);
  }

  bindDrag(_onPet: () => void): void {}

  tick(): void {}

  hitSize(): { w: number; h: number } {
    return { w: 112, h: 112 };
  }

  getElement(): HTMLElement {
    return this.cat;
  }
}
