let PIECES;
try {
  PIECES = await fetch(new URL("shared/pieces.json", import.meta.url)).then((response) => {
    if (!response.ok) throw new Error("无法加载棋块定义");
    return response.json();
  });
} catch (error) {
  const message = document.querySelector("#lobbyMessage");
  if (message) {
    message.textContent = "页面资源加载失败，请通过 HTTP 服务访问并刷新。";
    message.classList.add("error");
  }
  throw error;
}

const PIECE_MAP = new Map(PIECES.map((piece) => [piece.id, piece]));
const SIDES = [[1, 0], [-1, 0], [0, 1], [0, -1]];
const CORNERS = [[1, 1], [1, -1], [-1, 1], [-1, -1]];
const STORAGE_KEY = "blokus-online-session-v1";
const NAME_KEY = "blokus-online-name";
const API_BASE = location.pathname.startsWith("/blokus") ? "/blokus/api" : "/api";
const COLOR_VALUES = {
  blue: "var(--blue)",
  yellow: "var(--yellow)",
  red: "var(--red)",
  green: "var(--green)",
};
const COLOR_LABELS = {
  blue: "蓝方",
  yellow: "黄方",
  red: "红方",
  green: "绿方",
};
const COLOR_SLOTS = {
  2: ["blue", "red"],
  3: ["blue", "yellow", "red"],
  4: ["blue", "yellow", "red", "green"],
};
const OFFLINE_BOARD_SIZES = { 2: 14, 3: 17, 4: 20 };

const dom = Object.fromEntries([
  "lobbyScreen", "waitingScreen", "gameShell", "createForm", "joinForm",
  "createName", "joinName", "joinCode", "lobbyMessage", "waitingMessage",
  "offlineModeButton", "offlineModeMeta",
  "waitingRoomCode", "seatGrid", "startGameButton", "leaveWaitingButton",
  "copyRoomCodeButton", "roomBadge", "roomCodeHeader", "connectionDot",
  "board", "playerList", "pieceBank", "pieceCount", "turnName", "turnDot",
  "turnNumber", "turnEyebrow", "statusText", "statusIcon", "selectedPreview",
  "selectedName", "selectedMeta", "rotateButton", "flipButton",
  "clearSelectionButton", "confirmPlacementButton", "passButton", "copyGameRoomButton",
  "rulesButton", "rulesDialog", "resultDialog", "resultTitle", "resultSubtitle",
  "rankingList", "rematchButton", "resultLobbyButton",
].map((id) => [id, document.querySelector(`#${id}`)]));
dom.statusBar = document.querySelector(".status-bar");

let session = loadSession();
let room = null;
let selectedPiece = null;
let rotation = 0;
let flipped = false;
let hoverAnchor = null;
let previewIndexes = [];
let cellNodes = [];
let streamController = null;
let streamGeneration = 0;
let actionPending = false;
let lastEventId = null;
let offlineMode = false;
let pendingAnchor = null;
let pendingPlacementValid = false;
let dragPointerId = null;

function loadSession() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY));
  } catch {
    return null;
  }
}

function saveSession(value) {
  session = value;
  if (value) localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  else localStorage.removeItem(STORAGE_KEY);
}

function rememberName(name) {
  localStorage.setItem(NAME_KEY, name);
}

function authHeaders() {
  return session
    ? { "X-Player-Id": session.playerId, "X-Player-Token": session.token }
    : {};
}

async function api(path, { method = "GET", body, auth = true } = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(auth ? authHeaders() : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.message || "服务器请求失败");
    error.code = data.error;
    error.room = data.room;
    error.status = response.status;
    throw error;
  }
  return data;
}

function currentBoardSize() {
  return room?.game?.boardSize || 20;
}

function boardIndex(x, y) {
  return y * currentBoardSize() + x;
}

function inBounds(x, y) {
  const size = currentBoardSize();
  return x >= 0 && x < size && y >= 0 && y < size;
}

function normalize(cells) {
  const minX = Math.min(...cells.map(([x]) => x));
  const minY = Math.min(...cells.map(([, y]) => y));
  return cells
    .map(([x, y]) => [x - minX, y - minY])
    .sort(([ax, ay], [bx, by]) => ay - by || ax - bx);
}

function transform(cells, turns = 0, isFlipped = false) {
  let result = cells.map(([x, y]) => [isFlipped ? -x : x, y]);
  for (let step = 0; step < turns; step += 1) {
    result = result.map(([x, y]) => [-y, x]);
  }
  return normalize(result);
}

function atPosition(shape, anchorX, anchorY) {
  return shape.map(([x, y]) => [anchorX + x, anchorY + y]);
}

function placementCells(shape, anchorX, anchorY, player) {
  const [cornerX, cornerY] = player.corner;
  if (!player.placements.length && anchorX === cornerX && anchorY === cornerY) {
    const maxX = Math.max(...shape.map(([x]) => x));
    const maxY = Math.max(...shape.map(([, y]) => y));
    return atPosition(
      shape,
      cornerX === currentBoardSize() - 1 ? cornerX - maxX : cornerX,
      cornerY === currentBoardSize() - 1 ? cornerY - maxY : cornerY,
    );
  }
  return atPosition(shape, anchorX, anchorY);
}

function validate(cells, player) {
  const game = room?.game;
  if (!game) return { ok: false, reason: "棋局尚未开始。" };
  if (cells.some(([x, y]) => !inBounds(x, y))) {
    return { ok: false, reason: "棋块超出棋盘，请换一个落点。" };
  }
  if (cells.some(([x, y]) => game.board[boardIndex(x, y)] !== null)) {
    return { ok: false, reason: "这里已有棋块，不能重叠。" };
  }
  for (const [x, y] of cells) {
    for (const [dx, dy] of SIDES) {
      const nx = x + dx;
      const ny = y + dy;
      if (inBounds(nx, ny) && game.board[boardIndex(nx, ny)] === player.color) {
        return { ok: false, reason: "同色棋块不能边贴边，只能角接角。" };
      }
    }
  }
  if (!player.placements.length) {
    const [cornerX, cornerY] = player.corner;
    const covers = cells.some(([x, y]) => x === cornerX && y === cornerY);
    return covers
      ? { ok: true, reason: "合法落点" }
      : { ok: false, reason: `第一块棋必须覆盖${player.colorLabel}的起始角。` };
  }
  const touches = cells.some(([x, y]) => CORNERS.some(([dx, dy]) => {
    const nx = x + dx;
    const ny = y + dy;
    return inBounds(nx, ny) && game.board[boardIndex(nx, ny)] === player.color;
  }));
  return touches
    ? { ok: true, reason: "合法落点" }
    : { ok: false, reason: "棋块必须与至少一个同色棋块角接角。" };
}

function myPlayer() {
  if (offlineMode) return turnPlayer();
  return room?.game?.players.find((player) => player.id === session?.playerId) || null;
}

function roomMe() {
  return room?.players.find((player) => player.id === session?.playerId) || null;
}

function turnPlayer() {
  if (!room?.game) return null;
  return room.game.players[room.game.currentPlayer] || null;
}

function canAct() {
  return Boolean(
    room?.status === "playing"
    && (offlineMode || turnPlayer()?.id === session?.playerId)
    && !turnPlayer()?.out
    && !actionPending,
  );
}

function canResign() {
  const player = myPlayer();
  return Boolean(room?.status === "playing" && player && !player.out && !actionPending && (offlineMode || player.id === session?.playerId));
}

function pieceSize(pieceId) {
  return PIECE_MAP.get(pieceId).cells.length;
}

function refreshLocalScores() {
  if (!room?.game) return;
  room.game.players.forEach((player) => {
    const remainingSquares = player.remaining.reduce((sum, pieceId) => sum + pieceSize(pieceId), 0);
    player.remainingSquares = remainingSquares;
    player.score = remainingSquares ? -remainingSquares : 15 + (player.lastPiece === "I1" ? 5 : 0);
  });
}

function createOfflineRoom(capacity = 4) {
  const colors = COLOR_SLOTS[capacity];
  const boardSize = OFFLINE_BOARD_SIZES[capacity];
  const corners = {
    blue: [0, 0],
    yellow: [boardSize - 1, 0],
    red: [boardSize - 1, boardSize - 1],
    green: [0, boardSize - 1],
  };
  const players = colors.map((color) => ({
    id: `offline-${color}`,
    name: COLOR_LABELS[color],
    color,
    colorLabel: COLOR_LABELS[color],
    corner: corners[color],
    isHost: false,
    connected: true,
    remaining: PIECES.map((piece) => piece.id),
    placements: [],
    out: false,
    lastPiece: null,
  }));
  const game = {
    boardSize,
    board: Array(boardSize * boardSize).fill(null),
    players,
    currentPlayer: 0,
    turn: 1,
    status: "playing",
    lastMove: [],
    winnerIds: [],
  };
  const offlineRoom = {
    code: "OFFLINE",
    capacity,
    status: "playing",
    version: 1,
    createdAt: Date.now() / 1000,
    updatedAt: Date.now() / 1000,
    players: players.map(({ id, name, color, colorLabel, corner }) => ({ id, name, color, colorLabel, corner, isHost: false, connected: true })),
    game,
    rematchVotes: [],
    lastEvent: null,
  };
  refreshLocalScores();
  return offlineRoom;
}

function localAdvanceTurn() {
  const game = room.game;
  if (game.players.every((player) => player.out)) {
    game.status = "finished";
    const best = Math.max(...game.players.map((player) => player.score));
    game.winnerIds = game.players.filter((player) => player.score === best).map((player) => player.id);
    room.status = "finished";
    return;
  }
  let next = game.currentPlayer;
  do {
    next = (next + 1) % game.players.length;
  } while (game.players[next].out);
  game.currentPlayer = next;
  game.turn += 1;
}

function localPlaceSelected(anchorX, anchorY) {
  const player = myPlayer();
  const pieceId = selectedPiece;
  const cells = placementCells(selectedShape(), anchorX, anchorY, player);
  const result = validate(cells, player);
  if (!result.ok) {
    setStatus(result.reason, "error");
    showPreview(anchorX, anchorY);
    return false;
  }
  cells.forEach(([x, y]) => { room.game.board[boardIndex(x, y)] = player.color; });
  player.remaining = player.remaining.filter((id) => id !== pieceId);
  player.placements.push({ pieceId, cells });
  player.lastPiece = pieceId;
  room.game.lastMove = cells;
  room.version += 1;
  room.updatedAt = Date.now() / 1000;
  refreshLocalScores();
  localAdvanceTurn();
  return true;
}

function localResign() {
  const player = myPlayer();
  if (!player || player.out) return;
  player.out = true;
  room.version += 1;
  room.updatedAt = Date.now() / 1000;
  room.lastEvent = {
    id: room.version,
    type: "PLAYER_RESIGNED",
    message: `${player.name} 已认输并结束，继续操作其他颜色。`,
    playerId: player.id,
    time: Math.floor(Date.now() / 1000),
  };
  localAdvanceTurn();
}

function selectedShape() {
  if (!selectedPiece) return [];
  return transform(PIECE_MAP.get(selectedPiece).cells, rotation, flipped);
}

function makeMiniPiece(cells, color) {
  const shape = normalize(cells);
  const width = Math.max(...shape.map(([x]) => x)) + 1;
  const height = Math.max(...shape.map(([, y]) => y)) + 1;
  const occupied = new Set(shape.map(([x, y]) => `${x},${y}`));
  const mini = document.createElement("span");
  mini.className = "mini-piece";
  mini.style.setProperty("--piece-color", COLOR_VALUES[color]);
  mini.style.gridTemplateColumns = `repeat(${width}, var(--mini-cell))`;
  mini.style.gridTemplateRows = `repeat(${height}, var(--mini-cell))`;
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const cell = document.createElement("i");
      cell.className = `mini-cell${occupied.has(`${x},${y}`) ? " on" : ""}`;
      mini.append(cell);
    }
  }
  return mini;
}

function buildBoard(size = 20) {
  const fragment = document.createDocumentFragment();
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const cell = document.createElement("button");
      cell.type = "button";
      cell.className = "cell";
      cell.dataset.x = x;
      cell.dataset.y = y;
      cell.setAttribute("role", "gridcell");
      cell.setAttribute("aria-label", `第 ${y + 1} 行，第 ${x + 1} 列`);
      if ((x === 0 || x === size - 1) && (y === 0 || y === size - 1)) {
        cell.classList.add("start-corner");
      }
      fragment.append(cell);
    }
  }
  dom.board.replaceChildren(fragment);
  dom.board.style.setProperty("--board-size", size);
  cellNodes = [...dom.board.children];
}

function showScreen(name) {
  dom.lobbyScreen.classList.toggle("hidden", name !== "lobby");
  dom.waitingScreen.classList.toggle("hidden", name !== "waiting");
  dom.gameShell.classList.toggle("hidden", name !== "game");
  dom.roomBadge.classList.toggle("hidden", name === "lobby");
}

function setMessage(node, message = "", error = false) {
  node.textContent = message;
  node.classList.toggle("error", error);
}

function setStatus(message, type = "info") {
  dom.statusText.textContent = message;
  dom.statusBar.classList.toggle("error", type === "error");
  dom.statusBar.classList.toggle("success", type === "success");
  dom.statusIcon.textContent = type === "error" ? "!" : type === "success" ? "✓" : "↗";
}

function setConnected(value) {
  dom.connectionDot.classList.toggle("online", value);
  dom.roomBadge.title = value ? "已连接，点击复制房间号" : "正在重连";
}

function renderWaiting() {
  showScreen("waiting");
  dom.waitingRoomCode.textContent = room.code;
  dom.roomCodeHeader.textContent = room.code;
  const slots = COLOR_SLOTS[room.capacity];
  const fragment = document.createDocumentFragment();
  slots.forEach((color, index) => {
    const player = room.players[index];
    const card = document.createElement("article");
    card.className = `seat-card${player ? "" : " empty"}`;
    card.style.setProperty("--seat-color", COLOR_VALUES[color]);
    card.innerHTML = `
      <span class="seat-color"></span>
      <span>
        <strong>${player ? escapeHtml(player.name) : "等待加入"}</strong>
        <small>${COLOR_LABELS[color]}${player?.isHost ? " · 房主" : ""}</small>
      </span>`;
    fragment.append(card);
  });
  dom.seatGrid.replaceChildren(fragment);

  const me = roomMe();
  const isFull = room.players.length === room.capacity;
  dom.startGameButton.classList.toggle("hidden", !me?.isHost);
  dom.startGameButton.disabled = !isFull || actionPending;
  dom.startGameButton.textContent = isFull
    ? `开始 ${room.capacity} 人对局`
    : `等待玩家加入 ${room.players.length}/${room.capacity}`;
  setMessage(
    dom.waitingMessage,
    isFull
      ? `房间已满，将使用 ${room.capacity === 2 ? "14×14" : room.capacity === 3 ? "17×17" : "20×20"} 棋盘。`
      : `还差 ${room.capacity - room.players.length} 位玩家。`,
  );
}

function renderBoard() {
  if (!room?.game) return;
  const size = room.game.boardSize;
  if (cellNodes.length !== size * size) buildBoard(size);
  clearPreview();
  const activeColors = new Set(room.game.players.map((player) => player.color));
  const cornerIndexes = new Map([
    [0, "blue"],
    [size - 1, "yellow"],
    [(size - 1) * size, "green"],
    [size * size - 1, "red"],
  ]);
  const last = new Set(room.game.lastMove.map(([x, y]) => boardIndex(x, y)));
  cellNodes.forEach((cell, index) => {
    const color = room.game.board[index];
    cell.classList.toggle("filled", Boolean(color));
    cell.classList.toggle("last-move", last.has(index));
    cell.classList.toggle("inactive-corner", cornerIndexes.has(index) && !activeColors.has(cornerIndexes.get(index)));
    if (color) cell.dataset.player = color;
    else delete cell.dataset.player;
  });
  document.querySelectorAll(".corner-label").forEach((label) => {
    const color = [...label.classList].find((name) => name.startsWith("corner-"))?.slice(7);
    label.classList.toggle("inactive", !activeColors.has(color));
  });
}

function renderPlayers() {
  const fragment = document.createDocumentFragment();
  room.game.players.forEach((player, index) => {
    const active = index === room.game.currentPlayer && room.status === "playing";
    const chip = document.createElement("article");
    chip.className = `player-chip${active ? " active" : ""}${player.connected ? "" : " offline"}${player.out ? " finished" : ""}`;
    chip.style.setProperty("--player-color", COLOR_VALUES[player.color]);
    chip.innerHTML = `
      <div class="chip-top">
        <strong>${escapeHtml(player.name)}${player.id === session.playerId ? " · 你" : ""}</strong>
        <span class="chip-status" title="${player.connected ? "在线" : "离线"}">${player.out ? "已结束" : player.connected ? "" : "离线"}</span>
      </div>
      <div class="chip-stats">
        <span>剩 <b>${player.remaining.length}</b> 块</span>
        <span>积分 <b>${player.score}</b></span>
      </div>`;
    fragment.append(chip);
  });
  dom.playerList.replaceChildren(fragment);
}

function renderPieces() {
  const player = myPlayer();
  const allowed = canAct();
  const fragment = document.createDocumentFragment();
  if (player) {
    for (const piece of PIECES) {
      if (!player.remaining.includes(piece.id)) continue;
      const button = document.createElement("button");
      button.type = "button";
      button.disabled = !allowed;
      button.className = `piece-button${selectedPiece === piece.id ? " selected" : ""}`;
      button.dataset.pieceId = piece.id;
      button.style.setProperty("--piece-color", COLOR_VALUES[player.color]);
      button.setAttribute("aria-label", `选择${piece.name}，${piece.cells.length}格`);
      button.title = `${piece.name} · ${piece.cells.length} 格`;
      button.append(makeMiniPiece(piece.cells, player.color));
      fragment.append(button);
    }
  }
  dom.pieceBank.replaceChildren(fragment);
  dom.pieceBank.classList.toggle("locked", !allowed);
  dom.pieceCount.textContent = player?.remaining.length ?? 0;
}

function renderSelection() {
  const piece = selectedPiece ? PIECE_MAP.get(selectedPiece) : null;
  const player = myPlayer();
  dom.selectedPreview.replaceChildren();
  if (piece && player) {
    dom.selectedPreview.append(makeMiniPiece(selectedShape(), player.color));
    dom.selectedName.textContent = piece.name;
    dom.selectedMeta.textContent = `${piece.cells.length} 格 · 拖动定位后确认`;
  } else {
    const empty = document.createElement("span");
    empty.className = "empty-selection";
    empty.textContent = "+";
    dom.selectedPreview.append(empty);
    dom.selectedName.textContent = "尚未选择";
    dom.selectedMeta.textContent = myPlayer()?.out ? "你已结束，等待其他玩家" : canAct() ? "点选下方任意棋块" : "等待你的回合";
  }
  dom.rotateButton.disabled = !piece || !canAct();
  dom.flipButton.disabled = !piece || !canAct();
  dom.confirmPlacementButton.disabled = !piece || !pendingAnchor || !pendingPlacementValid || !canAct();
  dom.board.classList.toggle("positioning", Boolean(piece && canAct()));
}

function renderTurn() {
  const player = turnPlayer();
  if (room.status === "finished") {
    const winners = room.game.players
      .filter((item) => room.game.winnerIds.includes(item.id))
      .map((item) => item.name)
      .join("、");
    dom.turnEyebrow.textContent = offlineMode ? "OFFLINE FINISHED" : "GAME FINISHED";
    dom.turnName.textContent = `${winners} 获胜`;
    dom.turnDot.style.background = "var(--green)";
    dom.passButton.disabled = true;
    setStatus(`本局结束。获胜者：${winners}。`, "success");
    renderResult();
    return;
  }
  const mine = offlineMode || player?.id === session.playerId;
  dom.turnEyebrow.textContent = offlineMode ? "OFFLINE TURN" : mine ? "YOUR TURN" : "CURRENT TURN";
  dom.turnName.textContent = mine ? `${player.name}，轮到你` : `等待 ${player.name}`;
  dom.turnDot.style.background = COLOR_VALUES[player.color];
  dom.turnNumber.textContent = String(room.game.turn).padStart(2, "0");
  dom.passButton.disabled = !canResign();
  dom.passButton.textContent = myPlayer()?.out ? "你已结束本局" : "认输并结束";
  if (mine) {
    setStatus(player.placements.length
      ? "选择棋块，与同色棋块角接角。"
      : `首块棋必须覆盖${player.colorLabel}的起始角。`);
  } else {
    setStatus(`正在等待 ${player.name} 落子。`);
  }
}

function renderGame() {
  showScreen("game");
  dom.roomCodeHeader.textContent = offlineMode ? "离线" : room.code;
  setConnected(!offlineMode);
  renderBoard();
  renderPlayers();
  renderPieces();
  renderSelection();
  renderTurn();
}

function applyRoom(nextRoom) {
  const wasFinished = room?.status === "finished";
  const incomingEvent = nextRoom?.lastEvent;
  const isNewEvent = incomingEvent && incomingEvent.id !== lastEventId;
  room = nextRoom;
  if (!room) return;
  if (wasFinished && room.status === "playing") {
    selectedPiece = null;
    rotation = 0;
    flipped = false;
    if (dom.resultDialog.open) dom.resultDialog.close();
  }
  dom.roomCodeHeader.textContent = room.code;
  if (room.status === "waiting") renderWaiting();
  else renderGame();
  if (isNewEvent) {
    lastEventId = incomingEvent.id;
    if (["PLAYER_RESIGNED", "PLAYER_OFFLINE", "PLAYER_RECONNECTED", "PLAYER_LEFT"].includes(incomingEvent.type)) {
      setStatus(incomingEvent.message, incomingEvent.type === "PLAYER_OFFLINE" ? "error" : "success");
    }
  }
}

function renderResult() {
  if (!room?.game || room.status !== "finished") return;
  const ranking = [...room.game.players].sort((a, b) => b.score - a.score);
  const best = ranking[0]?.score;
  const winners = ranking.filter((player) => player.score === best).map((player) => player.name);
  dom.resultTitle.textContent = winners.length > 1 ? "并列获胜" : `${winners[0]} 获胜`;
  const votes = room.rematchVotes || [];
  const voted = votes.includes(session.playerId);
  const roomIntact = room.players.length === room.capacity;
  dom.resultSubtitle.textContent = votes.length
    ? `再来一盘：${votes.length}/${room.players.length} 位玩家已接受。`
    : `本局使用 ${room.game.boardSize}×${room.game.boardSize} 棋盘，按剩余方格结算。`;
  const fragment = document.createDocumentFragment();
  ranking.forEach((player, index) => {
    const row = document.createElement("article");
    row.className = "ranking-row";
    row.style.setProperty("--player-color", COLOR_VALUES[player.color]);
    row.innerHTML = `
      <span class="ranking-position">${index + 1}</span>
      <span class="ranking-player"><strong>${escapeHtml(player.name)}</strong><small>${player.colorLabel} · 剩余 ${player.remainingSquares} 格</small></span>
      <b class="ranking-score">${player.score}</b>`;
    fragment.append(row);
  });
  dom.rankingList.replaceChildren(fragment);
  dom.rematchButton.disabled = voted || !roomIntact || actionPending;
  dom.rematchButton.textContent = !roomIntact
    ? "已有玩家离开"
    : voted
      ? `等待其他玩家 ${votes.length}/${room.players.length}`
      : "邀请再来一盘";
  if (!dom.resultDialog.open) dom.resultDialog.showModal();
}

function clearSelection() {
  selectedPiece = null;
  rotation = 0;
  flipped = false;
  hoverAnchor = null;
  pendingAnchor = null;
  pendingPlacementValid = false;
  clearPreview();
  if (room?.game) {
    renderPieces();
    renderSelection();
  }
}

function selectPiece(pieceId) {
  const player = myPlayer();
  if (!canAct() || !player?.remaining.includes(pieceId)) return;
  selectedPiece = pieceId;
  rotation = 0;
  flipped = false;
  pendingAnchor = null;
  pendingPlacementValid = false;
  renderPieces();
  renderSelection();
  setStatus(`已选择${PIECE_MAP.get(pieceId).name}。在棋盘上拖动或轻点定位，再确认落子。`);
  if (hoverAnchor) showPreview(hoverAnchor.x, hoverAnchor.y);
}

function clearPreview() {
  for (const index of previewIndexes) {
    const node = cellNodes[index];
    node?.classList.remove("preview", "invalid");
    if (node) delete node.dataset.previewPlayer;
  }
  previewIndexes = [];
}

function showPreview(anchorX, anchorY) {
  clearPreview();
  const player = myPlayer();
  if (!selectedPiece || !player || !canAct()) return;
  const cells = placementCells(selectedShape(), anchorX, anchorY, player);
  const result = validate(cells, player);
  for (const [x, y] of cells) {
    if (!inBounds(x, y)) continue;
    const index = boardIndex(x, y);
    const node = cellNodes[index];
    node.classList.add("preview");
    node.classList.toggle("invalid", !result.ok);
    node.dataset.previewPlayer = player.color;
    previewIndexes.push(index);
  }
  return result;
}

function setPendingAnchor(anchorX, anchorY) {
  if (!selectedPiece || !canAct()) return;
  pendingAnchor = { x: anchorX, y: anchorY };
  hoverAnchor = pendingAnchor;
  const result = showPreview(anchorX, anchorY);
  pendingPlacementValid = Boolean(result?.ok);
  dom.confirmPlacementButton.disabled = !pendingPlacementValid;
  setStatus(result?.ok ? "位置可用，点击“确认落子”完成操作。" : result?.reason || "当前位置不可用。", result?.ok ? "success" : "error");
}

function rotateSelected() {
  if (!selectedPiece || !canAct()) return;
  rotation = (rotation + 1) % 4;
  renderSelection();
  if (pendingAnchor) setPendingAnchor(pendingAnchor.x, pendingAnchor.y);
  else if (hoverAnchor) showPreview(hoverAnchor.x, hoverAnchor.y);
}

function flipSelected() {
  if (!selectedPiece || !canAct()) return;
  flipped = !flipped;
  renderSelection();
  if (pendingAnchor) setPendingAnchor(pendingAnchor.x, pendingAnchor.y);
  else if (hoverAnchor) showPreview(hoverAnchor.x, hoverAnchor.y);
}

async function confirmPlacement() {
  if (!pendingAnchor || !pendingPlacementValid) return;
  const pieceId = selectedPiece;
  dom.confirmPlacementButton.disabled = true;
  await placeSelected(pendingAnchor.x, pendingAnchor.y);
  if (selectedPiece !== pieceId) {
    pendingAnchor = null;
    pendingPlacementValid = false;
  }
}

async function placeSelected(anchorX, anchorY) {
  const player = myPlayer();
  if (!selectedPiece || !player || !canAct()) {
    setStatus(canAct() ? "请先选择一个棋块。" : "现在不是你的回合。", "error");
    return;
  }
  const cells = placementCells(selectedShape(), anchorX, anchorY, player);
  const localResult = validate(cells, player);
  if (!localResult.ok) {
    setStatus(localResult.reason, "error");
    showPreview(anchorX, anchorY);
    return;
  }
  if (offlineMode) {
    actionPending = true;
    const placed = localPlaceSelected(anchorX, anchorY);
    actionPending = false;
    if (placed) {
      selectedPiece = null;
      rotation = 0;
      flipped = false;
      applyRoom(room);
      setStatus("落子已记录，继续操作下一种颜色。", "success");
    }
    return;
  }
  actionPending = true;
  renderPieces();
  dom.passButton.disabled = true;
  let errorMessage = "";
  try {
    const result = await api(`/rooms/${room.code}/place`, {
      method: "POST",
      body: {
        pieceId: selectedPiece,
        rotation,
        flipped,
        anchorX,
        anchorY,
        expectedVersion: room.version,
      },
    });
    selectedPiece = null;
    rotation = 0;
    flipped = false;
    applyRoom(result.room);
    setStatus("落子已由服务器确认。", "success");
  } catch (error) {
    if (error.room) applyRoom(error.room);
    errorMessage = error.message;
  } finally {
    actionPending = false;
    if (room?.game) {
      renderPieces();
      renderSelection();
      renderTurn();
    }
  }
  if (errorMessage) setStatus(errorMessage, "error");
}

async function createRoom(event) {
  event.preventDefault();
  const name = dom.createName.value.trim();
  const capacity = Number(new FormData(dom.createForm).get("capacity"));
  setMessage(dom.lobbyMessage, "正在创建房间…");
  try {
    const result = await api("/rooms", { method: "POST", body: { name, capacity }, auth: false });
    rememberName(name);
    saveSession(result.session);
    history.replaceState(null, "", `${location.pathname}?room=${result.session.roomCode}`);
    applyRoom(result.room);
    connectStream();
  } catch (error) {
    setMessage(dom.lobbyMessage, error.message, true);
  }
}

async function joinRoom(event) {
  event.preventDefault();
  const name = dom.joinName.value.trim();
  const code = dom.joinCode.value.trim().toUpperCase();
  setMessage(dom.lobbyMessage, "正在加入房间…");
  try {
    const result = await api(`/rooms/${code}/join`, { method: "POST", body: { name }, auth: false });
    rememberName(name);
    saveSession(result.session);
    history.replaceState(null, "", `${location.pathname}?room=${result.session.roomCode}`);
    applyRoom(result.room);
    connectStream();
  } catch (error) {
    setMessage(dom.lobbyMessage, error.message, true);
  }
}

function startOfflineMode() {
  const capacity = Number(new FormData(dom.createForm).get("capacity")) || 4;
  offlineMode = true;
  session = { roomCode: "OFFLINE", playerId: "offline-controller", token: "" };
  room = createOfflineRoom(capacity);
  selectedPiece = null;
  rotation = 0;
  flipped = false;
  history.replaceState(null, "", location.pathname);
  applyRoom(room);
  setStatus(`离线模式：你将依次操作 ${capacity} 种颜色。`, "success");
}

async function startGame() {
  if (actionPending) return;
  actionPending = true;
  renderWaiting();
  let errorMessage = "";
  try {
    const result = await api(`/rooms/${room.code}/start`, { method: "POST", body: {} });
    applyRoom(result.room);
  } catch (error) {
    errorMessage = error.message;
  } finally {
    actionPending = false;
    if (room?.status === "waiting") renderWaiting();
    else if (room?.game) renderGame();
  }
  if (errorMessage) setMessage(dom.waitingMessage, errorMessage, true);
}

async function resignMyGame() {
  if (!canResign()) return;
  if (offlineMode) {
    actionPending = true;
    localResign();
    clearSelection();
    actionPending = false;
    applyRoom(room);
    setStatus("当前颜色已结束，继续操作下一种颜色。", "success");
    return;
  }
  actionPending = true;
  renderTurn();
  setStatus("正在结束你的本局状态…");
  let errorMessage = "";
  try {
    const result = await api(`/rooms/${room.code}/resign`, {
      method: "POST",
      body: { expectedVersion: room.version },
    });
    clearSelection();
    applyRoom(result.room);
  } catch (error) {
    if (error.room) applyRoom(error.room);
    errorMessage = error.message;
  } finally {
    actionPending = false;
    if (room?.game) renderTurn();
  }
  if (errorMessage) setStatus(errorMessage, "error");
}

async function requestRematch() {
  if (!room || room.status !== "finished" || actionPending) return;
  if (offlineMode) {
    if (dom.resultDialog.open) dom.resultDialog.close();
    room = createOfflineRoom();
    selectedPiece = null;
    rotation = 0;
    flipped = false;
    applyRoom(room);
    setStatus("新一局离线棋局已开始。", "success");
    return;
  }
  actionPending = true;
  renderResult();
  try {
    const result = await api(`/rooms/${room.code}/rematch`, { method: "POST", body: {} });
    applyRoom(result.room);
  } catch (error) {
    dom.resultSubtitle.textContent = error.message;
  } finally {
    actionPending = false;
    if (room?.status === "finished") renderResult();
  }
}

async function leaveRoom() {
  if (!session || !room) return;
  if (offlineMode) {
    resetToLobby();
    return;
  }
  const shouldLeave = room.status === "playing"
    ? window.confirm("退出后将视为认输，确定离开当前房间吗？")
    : true;
  if (!shouldLeave) return;
  try {
    await api(`/rooms/${room.code}/leave`, { method: "POST", body: {} });
  } catch {
    // Local exit must remain possible even when the network is unavailable.
  }
  resetToLobby();
}

function resetToLobby(message = "") {
  streamGeneration += 1;
  streamController?.abort();
  streamController = null;
  saveSession(null);
  room = null;
  offlineMode = false;
  selectedPiece = null;
  lastEventId = null;
  if (dom.resultDialog.open) dom.resultDialog.close();
  history.replaceState(null, "", location.pathname);
  setConnected(false);
  showScreen("lobby");
  setMessage(dom.lobbyMessage, message);
}

async function copyInvite() {
  if (!room) return;
  if (offlineMode) {
    setStatus("离线模式不需要邀请链接。", "success");
    return;
  }
  const url = `${location.origin}${location.pathname}?room=${room.code}`;
  try {
    await navigator.clipboard.writeText(url);
    if (room.status === "waiting") setMessage(dom.waitingMessage, "邀请链接已复制。");
    else setStatus("邀请链接已复制。", "success");
  } catch {
    window.prompt("复制这个邀请链接", url);
  }
}

async function connectStream() {
  const generation = ++streamGeneration;
  streamController?.abort();
  streamController = new AbortController();

  while (session && generation === streamGeneration) {
    try {
      const response = await fetch(`${API_BASE}/rooms/${session.roomCode}/events`, {
        headers: authHeaders(),
        cache: "no-store",
        signal: streamController.signal,
      });
      if (response.status === 401 || response.status === 404) {
        resetToLobby("房间已失效，请重新加入。");
        return;
      }
      if (!response.ok || !response.body) throw new Error("实时连接失败");
      setConnected(true);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (generation === streamGeneration) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let boundary;
        while ((boundary = buffer.indexOf("\n\n")) >= 0) {
          const event = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const dataLine = event.split("\n").find((line) => line.startsWith("data: "));
          if (dataLine) applyRoom(JSON.parse(dataLine.slice(6)));
        }
      }
    } catch (error) {
      if (error.name === "AbortError") return;
    }
    setConnected(false);
    await new Promise((resolve) => window.setTimeout(resolve, 1500));
  }
}

async function restoreSession() {
  if (!session?.roomCode || !session?.playerId || !session?.token) {
    showScreen("lobby");
    return;
  }
  try {
    const result = await api(`/rooms/${session.roomCode}`);
    applyRoom(result.room);
    connectStream();
  } catch {
    resetToLobby("上次房间已经失效，请创建或加入新房间。");
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

dom.createForm.addEventListener("submit", createRoom);
dom.joinForm.addEventListener("submit", joinRoom);
dom.offlineModeButton.addEventListener("click", startOfflineMode);
dom.startGameButton.addEventListener("click", startGame);
dom.leaveWaitingButton.addEventListener("click", leaveRoom);
dom.copyRoomCodeButton.addEventListener("click", copyInvite);
dom.copyGameRoomButton.addEventListener("click", copyInvite);
dom.roomBadge.addEventListener("click", copyInvite);
dom.passButton.addEventListener("click", resignMyGame);
dom.rematchButton.addEventListener("click", requestRematch);
dom.resultLobbyButton.addEventListener("click", leaveRoom);
dom.rotateButton.addEventListener("click", rotateSelected);
dom.flipButton.addEventListener("click", flipSelected);
dom.clearSelectionButton.addEventListener("click", clearSelection);
dom.confirmPlacementButton.addEventListener("click", confirmPlacement);
dom.rulesButton.addEventListener("click", () => dom.rulesDialog.showModal());

document.querySelectorAll(".lobby-tab").forEach((button) => {
  button.addEventListener("click", () => {
    const isCreate = button.dataset.lobbyTab === "create";
    dom.createForm.classList.toggle("hidden", !isCreate);
    dom.joinForm.classList.toggle("hidden", isCreate);
    document.querySelectorAll(".lobby-tab").forEach((item) => {
      const active = item === button;
      item.classList.toggle("active", active);
      item.setAttribute("aria-selected", String(active));
    });
    setMessage(dom.lobbyMessage);
  });
});

document.querySelectorAll(".theme-button").forEach((button) => {
  button.addEventListener("click", () => {
    document.body.dataset.theme = button.dataset.themeValue;
    document.querySelectorAll(".theme-button").forEach((item) => item.classList.toggle("active", item === button));
  });
});

document.querySelectorAll('input[name="capacity"]').forEach((input) => {
  input.addEventListener("change", () => {
    const capacity = Number(input.value);
    dom.offlineModeMeta.textContent = `离线 ${capacity} 人：一人轮流操作 ${capacity} 种颜色`;
  });
});

dom.joinCode.addEventListener("input", () => {
  dom.joinCode.value = dom.joinCode.value.toUpperCase().replace(/[^A-Z2-9]/g, "").slice(0, 6);
});

dom.pieceBank.addEventListener("click", (event) => {
  const button = event.target.closest(".piece-button");
  if (button) selectPiece(button.dataset.pieceId);
});

dom.board.addEventListener("pointerover", (event) => {
  if (dragPointerId !== null || event.pointerType === "touch") return;
  const cell = event.target.closest(".cell");
  if (!cell) return;
  hoverAnchor = { x: Number(cell.dataset.x), y: Number(cell.dataset.y) };
  showPreview(hoverAnchor.x, hoverAnchor.y);
});

dom.board.addEventListener("pointerleave", () => {
  if (dragPointerId !== null) return;
  hoverAnchor = null;
  if (pendingAnchor) showPreview(pendingAnchor.x, pendingAnchor.y);
  else clearPreview();
});

dom.board.addEventListener("pointerdown", (event) => {
  if (!selectedPiece || !canAct()) return;
  const cell = event.target.closest(".cell");
  if (!cell) return;
  event.preventDefault();
  dragPointerId = event.pointerId;
  dom.board.setPointerCapture(event.pointerId);
  setPendingAnchor(Number(cell.dataset.x), Number(cell.dataset.y));
});

dom.board.addEventListener("pointermove", (event) => {
  if (event.pointerId !== dragPointerId) return;
  event.preventDefault();
  const cell = document.elementFromPoint(event.clientX, event.clientY)?.closest(".cell");
  if (cell && dom.board.contains(cell)) setPendingAnchor(Number(cell.dataset.x), Number(cell.dataset.y));
});

function finishBoardDrag(event) {
  if (event.pointerId !== dragPointerId) return;
  dragPointerId = null;
  if (dom.board.hasPointerCapture(event.pointerId)) dom.board.releasePointerCapture(event.pointerId);
  if (pendingPlacementValid) setStatus("位置已保留，点击“确认落子”完成操作。", "success");
}

dom.board.addEventListener("pointerup", finishBoardDrag);
dom.board.addEventListener("pointercancel", finishBoardDrag);

dom.board.addEventListener("click", (event) => {
  const cell = event.target.closest(".cell");
  if (cell && dragPointerId === null) setPendingAnchor(Number(cell.dataset.x), Number(cell.dataset.y));
});

document.addEventListener("keydown", (event) => {
  if (event.target.matches("input")) return;
  if (event.key.toLowerCase() === "r") rotateSelected();
  if (event.key.toLowerCase() === "f") flipSelected();
  if (event.key === "Escape" && !dom.rulesDialog.open) clearSelection();
  if (event.key === "?" && !dom.rulesDialog.open) dom.rulesDialog.showModal();
});

const savedName = localStorage.getItem(NAME_KEY) || "";
dom.createName.value = savedName;
dom.joinName.value = savedName;
const roomFromUrl = new URLSearchParams(location.search).get("room");
if (roomFromUrl && !session) {
  dom.joinCode.value = roomFromUrl.toUpperCase().slice(0, 6);
  document.querySelector('[data-lobby-tab="join"]').click();
}

buildBoard();
restoreSession();
