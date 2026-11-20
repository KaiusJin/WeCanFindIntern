function createDebouncedAction(action) {
  let timer = null;
  return (waitMs) => {
    clearTimeout(timer);
    timer = setTimeout(action, waitMs);
  };
}

export { createDebouncedAction };
