const express = require('express');
const cors = require('cors');
const config = require('./config');
const mrpeasyRoutes = require('./routes/mrpeasyRoutes');

const app = express();

// Middleware
app.use(cors({ origin: config.cors.origin }));
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// Health check endpoint
app.get('/health', (req, res) => {
  res.json({ status: 'OK', message: 'MRPeasy Custom Portal Backend is running' });
});

// API Routes
app.use('/api', mrpeasyRoutes);

// Error handling middleware
app.use((err, req, res, next) => {
  console.error(err.stack);
  res.status(500).json({ success: false, error: 'Internal server error' });
});

// 404 handler
app.use((req, res) => {
  res.status(404).json({ success: false, error: 'Route not found' });
});

const PORT = config.server.port;
app.listen(PORT, () => {
  console.log(`🚀 MRPeasy Custom Portal Backend running on port ${PORT}`);
  console.log(`Environment: ${config.server.env}`);
});
