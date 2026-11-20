import { $ } from "./helpers.js?v=20260901-error-dialog-minimal-v1";

function setupInfiniteScroll({
  sentinelSelector = "#infinite-scroll-sentinel",
  rootSelector = null,
  isLoading,
  canLoadMore,
  loadMore,
  rootMargin = "400px",
} = {}) {
  const sentinel = $(sentinelSelector);
  if (!sentinel || !isLoading || !canLoadMore || !loadMore) return null;
  const root = rootSelector ? $(rootSelector) : null;

  const observer = new IntersectionObserver(
    ([entry]) => {
      if (entry?.isIntersecting && !isLoading() && canLoadMore()) {
        loadMore();
      }
    },
    { root, rootMargin, threshold: 0 },
  );
  observer.observe(sentinel);
  return observer;
}

export { setupInfiniteScroll };
