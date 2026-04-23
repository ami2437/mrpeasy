import React from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import Navbar from './components/Navbar';
import Dashboard from './pages/Dashboard';
import './App.css';

function App() {
  return (
    <Router>
      <div className="App">
        <Navbar />
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/orders" element={<Dashboard />} />
          <Route path="/inventory" element={<Dashboard />} />
          <Route path="/manufacturing" element={<Dashboard />} />
          <Route path="/vendors" element={<Dashboard />} />
        </Routes>
      </div>
    </Router>
  );
}

export default App;
