require('dotenv').config();

const config = {
  mrpeasy: {
    baseUrl: process.env.MRPEASY_API_BASE_URL || 'https://api.mrpeasy.com/rest/v1',
    apiKey: process.env.MRPEASY_API_KEY,
    apiSecret: process.env.MRPEASY_API_SECRET,
  },
  server: {
    port: process.env.PORT || 5000,
    env: process.env.NODE_ENV || 'development',
  },
  cors: {
    origin: process.env.CORS_ORIGIN || 'http://localhost:3000',
  },
};

module.exports = config;
