import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { LiveSessionProvider } from './LiveSessionContext'
import { LiveAssistant } from './routes/LiveAssistant'
import { SessionDetail } from './routes/SessionDetail'
import { SessionsList } from './routes/SessionsList'

export default function App() {
  return (
    <BrowserRouter>
      {/* Provider sits ABOVE Routes so switching routes never unmounts the
          live WebSocket/session — only "New session" starts a fresh one. */}
      <LiveSessionProvider>
        <Routes>
          <Route path="/" element={<LiveAssistant />} />
          <Route path="/sessions" element={<SessionsList />} />
          <Route path="/sessions/:id" element={<SessionDetail />} />
        </Routes>
      </LiveSessionProvider>
    </BrowserRouter>
  )
}
