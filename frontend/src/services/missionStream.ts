const WS_BASE_URL = 'ws://localhost:8000'

export function connectMissionStream(
  missionId: string,
  onMessage: (data: unknown) => void,
) {
  const socket = new WebSocket(
    `${WS_BASE_URL}/missions/${missionId}/stream`,
  )

  socket.onmessage = (event) => {
    const data = JSON.parse(event.data)
    onMessage(data)
  }

  return socket
}