/**
 * FastAPI answers a failure with a `detail` that is either a sentence or a map
 * of field errors, and ofetch hands the parsed body back on the response. Both
 * shapes are folded down to the one line a screen can print.
 */
export const apiErrorMessage = (error: any): string | null => {
  const detail = error?.response?._data?.detail ?? error?.data?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const first = Object.values(detail)[0];
    if (typeof first === "string") return first;
  }
  return null;
};

/** The status the API answered with, when the request reached it at all. */
export const apiErrorStatus = (error: any): number | undefined =>
  error?.response?.status ?? error?.status;

/** True while the request was dropped on purpose, which nobody needs told about. */
export const isAbortError = (error: any): boolean =>
  error?.name === "AbortError" || error?.cause?.name === "AbortError";
