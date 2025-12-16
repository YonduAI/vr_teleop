const http = require("http");
const WebSocket = require("ws");

const PORT = process.env.PORT || 3000;
const SHARED_SECRET = process.env.SIGNALING_TOKEN || ""; // must be set

// Basic HTTP server (optional)
const server = http.createServer((req, res) => {
  res.writeHead(200, { "Content-Type": "text/plain" });
  res.end("Signaling server is running.\n");
});

const wss = new WebSocket.Server({ server });

/**
 * rooms: Map<string, Set<WebSocket>>
 */
const rooms = new Map();

function joinRoom(ws, roomId) {
  if (!rooms.has(roomId)) rooms.set(roomId, new Set());
  const set = rooms.get(roomId);

  // leave previous room if any
  if (ws.roomId && ws.roomId !== roomId) {
    leaveRoom(ws);
  }

  set.add(ws);
  ws.roomId = roomId;
  console.log(`Client joined room=${roomId}. total=${set.size}`);
}

function leaveRoom(ws) {
  const roomId = ws.roomId;
  if (!roomId) return;

  const set = rooms.get(roomId);
  if (set) {
    set.delete(ws);
    console.log(`Client left room=${roomId}. remaining=${set.size}`);
    if (set.size === 0) rooms.delete(roomId);
  }
  ws.roomId = null;
}

function broadcastToRoom(roomId, data, exceptWs = null) {
  const set = rooms.get(roomId);
  if (!set) return;

  const msg = JSON.stringify(data);
  for (const client of set) {
    if (client.readyState === WebSocket.OPEN && client !== exceptWs) {
      client.send(msg);
    }
  }
}

// Heartbeat to clean dead connections
function heartbeat() {
  this.isAlive = true;
}

wss.on("connection", (ws) => {
  console.log("New WebSocket connection.");
  ws.isAuthed = false;
  ws.roomId = null;
  ws.isAlive = true;
  ws.on("pong", heartbeat);

  ws.on("message", (message) => {
    const text = Buffer.isBuffer(message) ? message.toString("utf8") : message;

    let data;
    try {
      data = JSON.parse(text);
    } catch (err) {
      console.error("Invalid JSON:", err, "raw=", text);
      return;
    }

    const { type, roomId, payload } = data;

    // 1) auth
    if (type === "auth") {
      const token = payload && payload.token;
      if (!SHARED_SECRET || token !== SHARED_SECRET) {
        console.warn("Invalid auth token, closing.");
        ws.close(1008, "Unauthorized");
        return;
      }
      ws.isAuthed = true;
      ws.send(JSON.stringify({ type: "auth-ok" }));
      console.log("Client authenticated.");
      return;
    }

    // 2) block everything else until authed
    if (!ws.isAuthed) {
      console.warn("Unauthenticated message, closing.");
      ws.close(1008, "Unauthorized");
      return;
    }

    // 3) signaling
    switch (type) {
      case "join":
        if (!roomId) return;
        joinRoom(ws, roomId);
        break;

      case "offer":
      case "answer":
      case "ice-candidate":
        if (!ws.roomId) {
          console.warn("Client sent signaling without joining room.");
          return;
        }
        broadcastToRoom(ws.roomId, { type, payload }, ws);
        break;

      default:
        console.warn("Unknown message type:", type);
    }
  });

  ws.on("close", () => {
    leaveRoom(ws);
    console.log("WebSocket closed.");
  });

  ws.on("error", (err) => {
    console.warn("WebSocket error:", err.message);
  });
});

// ping clients periodically
const interval = setInterval(() => {
  for (const ws of wss.clients) {
    if (ws.isAlive === false) {
      leaveRoom(ws);
      ws.terminate();
      continue;
    }
    ws.isAlive = false;
    ws.ping();
  }
}, 30000);

wss.on("close", () => clearInterval(interval));

// IMPORTANT: listen on 0.0.0.0 for external reachability
server.listen(PORT, "0.0.0.0", () => {
  console.log(`Signaling server listening on port ${PORT}`);
  console.log(`SIGNALING_TOKEN is ${SHARED_SECRET ? "set" : "NOT set (auth will fail)"}`);
});
