import { $ } from "./helpers.js?v=20260901-error-dialog-minimal-v1";

function setupInfiniteScroll({
  sentinelSelector = "#infinite-scroll-sentinel",
  isLoading,
  canLoadMore,
  loadMore,
  rootMargin = "400px",
} = {}) {
  const sentinel = $(sentinelSelector);
  if (!sentinel || !isLoading || !canLoadMore || !loadMore) return null;

  const observer = new IntersectionObserver(
    ([entry]) => {
      if (entry?.isIntersecting && !isLoading() && canLoadMore()) {
        loadMore();
      }
    },
    { root: null, rootMargin, threshold: 0 },
  );
  observer.observe(sentinel);
  return observer;
}

export { setupInfiniteScroll };
