import type { PetState } from "../state";
import type { CatKind } from "./types";

export type Facing = "front" | "left" | "right";

/** Sprite pack shared by walker-style canvas cats. */
export interface SpritePack {
  frameWidth: number;
  frameHeight: number;
  preload(): Promise<void>;
  framesOf(name: string): HTMLImageElement[];
  resolveAnimation(base: string, facing: Facing): string;
}

export interface WalkerOptions {
  kind: CatKind;
  /** CSS selector for the mount wrapper (contains a canvas). */
  mountSelector: string;
  pack: SpritePack;
  /** Override state → animation base mapping. */
  stateAnimation?: Partial<Record<PetState, string>>;
  /**
   * When true, idle never turns to face the camera — grey sheets are side-view
   * art (front frames are just copies of right).
   */
  preferSideFacing?: boolean;
}
