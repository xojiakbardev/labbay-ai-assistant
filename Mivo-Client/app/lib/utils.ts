import type { ClassValue } from "clsx"
import { clsx } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** The URL itself if it is an absolute https:// URL, otherwise null. Anything
 * rendered as a link or media source from message data goes through this —
 * no javascript:, data: or plain-http URLs. */
export function safeHttpsUrl(raw: string | null | undefined): string | null {
  if (!raw) return null
  try {
    const url = new URL(raw)
    return url.protocol === "https:" ? url.href : null
  } catch {
    return null
  }
}

// What POST /products/media and /products/{id}/images accept (the server
// decides by magic bytes; this is the early, friendlier check).
export const IMAGE_UPLOAD_TYPES = ["image/jpeg", "image/png", "image/webp"]
export const IMAGE_UPLOAD_MAX_BYTES = 5 * 1024 * 1024

export function imageUploadProblem(file: File): "type" | "size" | null {
  if (!IMAGE_UPLOAD_TYPES.includes(file.type)) return "type"
  if (file.size > IMAGE_UPLOAD_MAX_BYTES) return "size"
  return null
}
