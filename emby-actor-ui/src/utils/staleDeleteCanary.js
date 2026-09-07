export const STALE_DELETE_CANARY_DEFAULT_SIZE = 10;
export const STALE_DELETE_CANARY_MIN_SIZE = 1;
export const STALE_DELETE_CANARY_MAX_SIZE = 100;

export function validateStaleDeleteCanarySize(value) {
  return Number.isInteger(value)
    && value >= STALE_DELETE_CANARY_MIN_SIZE
    && value <= STALE_DELETE_CANARY_MAX_SIZE;
}

export function staleDeleteCanarySelectedTotal(job) {
  const value = job?.selected_total;
  return Number.isInteger(value) && value >= 0 ? value : 0;
}
