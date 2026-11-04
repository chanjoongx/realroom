import { MotionConfig } from "motion/react";
import { Studio } from "./Studio";

/** The run viewer with its animation settings. App loads this file on its own, so the home page does
 *  not download the viewer or the animation library before it can show anything. */
export default function StudioRoute({ id }: { id: string }) {
  return (
    <MotionConfig reducedMotion="user">
      <Studio key={id} id={id} />
    </MotionConfig>
  );
}
