import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { LiveAssistant } from './routes/LiveAssistant'
import { SessionDetail } from './routes/SessionDetail'
import { SessionsList } from './routes/SessionsList'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<LiveAssistant />} />
        <Route path="/sessions" element={<SessionsList />} />
        <Route path="/sessions/:id" element={<SessionDetail />} />
      </Routes>
    </BrowserRouter>
  )
}
