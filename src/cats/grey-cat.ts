import { WalkerCat } from "./walker-cat";
import * as greySprites from "./grey-sprites";
import type { SpritePack } from "./sprite-pack";

const GREY_PACK: SpritePack = {
  frameWidth: greySprites.FRAME_WIDTH,
  frameHeight: greySprites.FRAME_HEIGHT,
  preload: greySprites.preload,
  framesOf: greySprites.framesOf,
  resolveAnimation: greySprites.resolveAnimation,
};

/** Grey Desk-Paw persona — same walker behavior, grey sprite sheet. */
export function createGreyCat(): WalkerCat {
  return new WalkerCat({
    kind: "grey",
    mountSelector: "#cat-grey",
    pack: GREY_PACK,
    preferSideFacing: true,
    stateAnimation: {
      // focus sheet while the agent is working
      WORKING: "working",
      WAITING: "sit",
      SLEEPING: "sleep",
      HAPPY: "happy",
      DONE: "happy",
      DRAGGED: "dragged",
      TYPING: "typing_calm",
      TYPING_AGGRESSIVE: "typing_aggressive",
      EXHAUSTED: "typing_exhausted",
      ERROR: "overheated",
      OVERHEATED: "overheated",
    },
  });
}
