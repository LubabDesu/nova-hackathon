import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import "./App.css";
import { AuthProvider } from "./contexts/AuthContext";
import ProtectedRoute from "./components/ProtectedRoute";
import LoginPage from "./pages/LoginPage";
import DashboardPage from "./pages/DashboardPage";
import PlanPage from "./pages/PlanPage";
import TripPage from "./pages/TripPage";
import JoinPage from "./pages/JoinPage";
import GroupWaitingPage from "./pages/GroupWaitingPage";
import GroupPlanPage from "./pages/GroupPlanPage";

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/join/:groupId" element={<JoinPage />} />
          <Route
            path="/dashboard"
            element={
              <ProtectedRoute>
                <DashboardPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/plan"
            element={
              <ProtectedRoute>
                <PlanPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/trips/:id"
            element={
              <ProtectedRoute>
                <TripPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/group/:groupId/waiting"
            element={
              <ProtectedRoute>
                <GroupWaitingPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/group/:groupId/plan"
            element={
              <ProtectedRoute>
                <GroupPlanPage />
              </ProtectedRoute>
            }
          />
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
