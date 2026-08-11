import type { PetState } from "../state";

export type CatKind = "classic" | "walker" | "grey";

export interface CatController {
  kind: CatKind;
  mount(root: HTMLElement): void;
  show(): void;
  hide(): void;
  setState(state: PetState): void;
  onCursor(
    screenX: number,
    screenY: number,
    winX: number,
    winY: number,
    winW: number,
    winH: number,
  ): number;
  onLocalPointer(offsetX: number, offsetY: number): number;
  tick(now: number): void | Promise<void>;
  hitSize(): { w: number; h: number };
  getElement(): HTMLElement;
}
