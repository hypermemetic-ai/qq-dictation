import type { OSType } from "../lib/utils/keyboard";

/** Return the only supported operating-system type. */
export function useOsType(): OSType {
  return "linux";
}
