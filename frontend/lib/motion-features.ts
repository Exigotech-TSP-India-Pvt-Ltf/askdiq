/**
 * Motion feature bundle, loaded asynchronously by `MotionProvider` so the
 * first paint ships no animation code. `domMax` (rather than `domAnimation`)
 * adds layout animations, which the shared `layoutId` indicators need: the
 * sidebar active bar and the sliding tab pills.
 */
import { domMax } from "motion/react";

export default domMax;
