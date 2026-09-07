// Presentation only: reflect existing DOM state without touching game state or requests.
(() => {
  const code = document.querySelector("#roomCodeHeader");
  const appearance = document.querySelector(".appearance-menu");
  const board = document.querySelector("#board");
  const ranking = document.querySelector("#rankingList");
  const selectionHint = document.querySelector("#selectedMeta");
  const reflectSelectionHint = () => {
    if (selectionHint?.textContent === "点选下方任意棋块") selectionHint.textContent = "选择一个棋块开始定位";
  };
  if (selectionHint) new MutationObserver(reflectSelectionHint).observe(selectionHint, { childList: true, characterData: true, subtree: true });
  reflectSelectionHint();
  const reflectMissingResultValues = () => {
    ranking?.querySelectorAll(".ranking-score, .ranking-player small").forEach((node) => {
      if (node.textContent.includes("undefined")) node.textContent = node.textContent.replaceAll("undefined", "—");
    });
  };
  if (ranking) new MutationObserver(reflectMissingResultValues).observe(ranking, { childList: true, characterData: true, subtree: true });
  reflectMissingResultValues();
  const reflectBoardLabel = () => {
    const size = Math.sqrt(board?.children.length || 0);
    if (Number.isInteger(size) && size > 0) board.setAttribute("aria-label", `${size} 乘 ${size} 角斗士棋棋盘`);
  };
  if (board) new MutationObserver(reflectBoardLabel).observe(board, { childList: true });
  reflectBoardLabel();
  const reflectOffline = () => {
    document.body.dataset.uiOffline = String(code?.textContent.trim() === "离线");
  };
  if (code) new MutationObserver(reflectOffline).observe(code, { childList: true, characterData: true, subtree: true });
  reflectOffline();
  appearance?.addEventListener("click", (event) => {
    if (event.target.closest(".theme-button")) appearance.open = false;
  });
  document.addEventListener("click", (event) => {
    if (appearance?.open && !appearance.contains(event.target)) appearance.open = false;
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && appearance?.open) {
      appearance.open = false;
      appearance.querySelector("summary").focus();
    }
  });
})();
