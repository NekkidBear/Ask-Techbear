import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Submission from './views/Submission'
import Dashboard from './views/Dashboard'
import Slideshow from './views/Slideshow'
import BatchReview from './views/BatchReview'

function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Public attendee-facing submission form */}
        <Route path="/submit" element={<Submission />} />

        {/* Moderator dashboard — localhost only */}
        <Route path="/dashboard" element={<Dashboard />} />

        {/* Async pipeline batch review — localhost only */}
        <Route path="/review" element={<BatchReview />} />

        {/* Slideshow display mode */}
        <Route path="/slideshow" element={<Slideshow />} />

        {/* Default redirect to submission form */}
        <Route path="/" element={<Navigate to="/submit" replace />}/>
      </Routes>
    </BrowserRouter>
  )
}

export default App