"use strict";

const http = require("http");
const crypto = require("crypto");
const WebSocket = require("ws");

const PORT = process.env.PORT || 3000;
const SHARED_SECRET = process.env.SIGNALING_TOKEN || ""; // must be set in env

// --- HTTP (optional healthcheck) ---
const server = http.createServer((req, res) => {
  res.writeHead(200, { "Content-Type": "text/plain" });
  res.end("Signaling server is running.\n");
});

// Limit memory blowups if someone spams big frames
const wss = new WebSocket.Server({
  server,
  maxPayload: 1 * 1024 * 1024, // 1MB
});

/**
 * rooms: Map<roomId, Map<clientId, ws>>
 */
const rooms = new Map();

function newId() {
  return crypto.randomBytes(12).toString("hex"); // 24 hex chars
}

function safeSend(ws, obj) {
  if (ws.readyState !== WebSocket.OPEN) return;
  try {
    ws.send(JSON.stringify(obj));
  } catch (e) {
    // ignore
  }
}

function getRoom(roomId) {
  if (!rooms.has(roomId)) rooms.set(roomId, new Map());
  return rooms.get(roomId);
}

function listPeers(roomId) {
  const room = rooms.get(roomId);
  if (!room) return [];
  const peers = [];
  for (const [clientId, ws] of room.entries()) {
    peers.push({
      clientId,
      role: ws.role || "unknown",
      name: ws.name || "",
    });
  }
  return peers;
}

function broadcast(roomId, obj, exceptWs = null) {
  const room = rooms.get(roomId);
  if (!room) return;
  for (const ws of room.values()) {
    if (ws.readyState === WebSocket.OPEN && ws !== exceptWs) {
      safeSend(ws, obj);
    }
  }
}

function sendTo(roomId, toClientId, obj) {
  const room = rooms.get(roomId);
  if (!room) return false;
  const ws = room.get(toClientId);
  if (!ws || ws.readyState !== WebSocket.OPEN) return false;
  safeSend(ws, obj);
  return true;
}

function findFirstByRole(roomId, role) {
  const room = rooms.get(roomId);
  if (!room) return null;
  for (const ws of room.values()) {
    if (
      (ws.role || "").toLowerCase() === role.toLowerCase() &&
      ws.readyState === WebSocket.OPEN
    ) {
      return ws;
    }
  }
  return null;
}

function findFirstNonRobot(roomId, exceptWs = null) {
  const room = rooms.get(roomId);
  if (!room) return null;
  for (const ws of room.values()) {
    if (ws === exceptWs) continue;
    if ((ws.role || "").toLowerCase() !== "robot" && ws.readyState === WebSocket.OPEN) {
      return ws;
    }
  }
  return null;
}

function joinRoom(ws, roomId, payload = {}) {
  if (!roomId) return;

  // Leave previous room if any
  if (ws.roomId && ws.roomId !== roomId) {
    leaveRoom(ws);
  }

  ws.roomId = roomId;
  ws.role =
    typeof payload.role === "string" ? payload.role : ws.role || "unknown";
  ws.name = typeof payload.name === "string" ? payload.name : ws.name || "";

  const room = getRoom(roomId);
  room.set(ws.clientId, ws);

  console.log(
    `join room=${roomId} clientId=${ws.clientId} role=${ws.role} total=${room.size}`,
  );

  // Tell joiner their id + current peers
  safeSend(ws, {
    type: "joined",
    roomId,
    payload: {
      clientId: ws.clientId,
      peers: listPeers(roomId),
    },
  });

  // Tell others about the new peer
  broadcast(
    roomId,
    {
      type: "peer-joined",
      roomId,
      payload: {
        clientId: ws.clientId,
        role: ws.role || "unknown",
        name: ws.name || "",
      },
    },
    ws,
  );
}

function leaveRoom(ws) {
  const roomId = ws.roomId;
  if (!roomId) return;

  const room = rooms.get(roomId);
  if (room) {
    const existed = room.delete(ws.clientId);
    if (existed) {
      console.log(
        `leave room=${roomId} clientId=${ws.clientId} remaining=${room.size}`,
      );
      broadcast(
        roomId,
        {
          type: "peer-left",
          roomId,
          payload: { clientId: ws.clientId },
        },
        ws,
      );
    }
    if (room.size === 0) rooms.delete(roomId);
  }

  ws.roomId = null;
}

// Heartbeat to clean dead connections
function heartbeat() {
  this.isAlive = true;
}

// --- Message routing rules ---
// We add `from` automatically.
// If message includes `to`, we direct it.
// Otherwise:
//   offer: prefer routing to role=robot (if exactly one robot, send there; else broadcast)
//   answer/ice-candidate: robot -> first non-robot, viewer/controller -> robot (or broadcast)
//   control/telemetry: you can direct with `to`, else broadcast
function routeMessage(ws, msg) {
  const roomId = ws.roomId;
  if (!roomId) return;

  const { type, payload } = msg;
  const to = typeof msg.to === "string" ? msg.to : null;

  const envelope = {
    type,
    roomId,
    from: ws.clientId,
    payload: payload ?? {},
  };

  // If explicit target provided, always respect it.
  if (to) {
    const ok = sendTo(roomId, to, envelope);
    if (!ok) {
      safeSend(ws, {
        type: "error",
        roomId,
        payload: { code: "NO_TARGET", to },
      });
    }
    return;
  }

  // Helper: route signaling to the first robot if present.
  const robot = findFirstByRole(roomId, "robot");

  // --- WebRTC signaling: default to robot if present ---
  if (type === "offer" || type === "answer" || type === "ice-candidate") {
    // Controller/viewer -> robot
    if (ws.role.toLowerCase() !== "robot") {
      if (robot && robot !== ws) {
        safeSend(robot, envelope);
        return;
      }
      // fallback if no robot found
      broadcast(roomId, envelope, ws);
      return;
    }

    // Robot -> first non-robot if available
    const viewer = findFirstNonRobot(roomId, ws);
    if (viewer) {
      safeSend(viewer, envelope);
      return;
    }

    // fallback to broadcast
    broadcast(roomId, envelope, ws);
    return;
  }

  // --- Control plane: default controller/viewer -> robot ---
  if (type === "control") {
    if (robot && robot !== ws) {
      safeSend(robot, envelope);
      return;
    }
    broadcast(roomId, envelope, ws);
    return;
  }

  // --- Ack/telemetry: default robot -> everyone else ---
  if (type === "control-ack" || type === "telemetry") {
    broadcast(roomId, envelope, ws);
    return;
  }

  // Default: broadcast to everyone else in room
  broadcast(roomId, envelope, ws);
}

wss.on("connection", (ws) => {
  ws.isAuthed = false;
  ws.roomId = null;
  ws.role = "unknown";
  ws.name = "";
  ws.clientId = newId();

  ws.isAlive = true;
  ws.on("pong", heartbeat);

  console.log(`New WebSocket connection clientId=${ws.clientId}`);

  ws.on("message", (message) => {
    const text = Buffer.isBuffer(message)
      ? message.toString("utf8")
      : String(message);

    let data;
    try {
      data = JSON.parse(text);
    } catch (err) {
      console.warn("Invalid JSON from clientId=", ws.clientId);
      return;
    }

    const type = data.type;

    // 1) auth
    if (type === "auth") {
      const token = data.payload && data.payload.token;
      if (!SHARED_SECRET || token !== SHARED_SECRET) {
        console.warn("Invalid auth token, closing. clientId=", ws.clientId);
        ws.close(1008, "Unauthorized");
        return;
      }
      ws.isAuthed = true;
      safeSend(ws, { type: "auth-ok", payload: { clientId: ws.clientId } });
      console.log("Client authenticated clientId=", ws.clientId);
      return;
    }

    // 2) block everything else until authed
    if (!ws.isAuthed) {
      ws.close(1008, "Unauthorized");
      return;
    }

    // 3) join
    if (type === "join") {
      const roomId = data.roomId;
      joinRoom(ws, roomId, data.payload || {});
      return;
    }

    // require join for anything else
    if (!ws.roomId) {
      safeSend(ws, { type: "error", payload: { code: "NOT_IN_ROOM" } });
      return;
    }

    // 4) helper: peer list request
    if (type === "peer-list") {
      safeSend(ws, {
        type: "peer-list",
        roomId: ws.roomId,
        payload: { peers: listPeers(ws.roomId) },
      });
      return;
    }

    // 5) routing for signaling + control
    switch (type) {
      case "offer":
      case "answer":
      case "ice-candidate":
      case "control":
      case "control-ack":
      case "telemetry":
      case "ping-app":
      case "pong-app":
        routeMessage(ws, data);
        return;

      default:
        // ignore unknown types but don't kill connection
        safeSend(ws, {
          type: "warn",
          roomId: ws.roomId,
          payload: { code: "UNKNOWN_TYPE", type },
        });
        return;
    }
  });

  ws.on("close", () => {
    leaveRoom(ws);
    console.log(`WebSocket closed clientId=${ws.clientId}`);
  });

  ws.on("error", (err) => {
    console.warn("WebSocket error clientId=", ws.clientId, err.message);
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

server.listen(PORT, "0.0.0.0", () => {
  console.log(`Signaling server listening on port ${PORT}`);
  console.log(
    `SIGNALING_TOKEN is ${SHARED_SECRET ? "set" : "NOT set (auth will fail)"}`,
  );
});
