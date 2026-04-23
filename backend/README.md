# MRPeasy Custom Manufacturing Portal - Backend

RESTful API backend for integrating with MRPeasy API and providing custom data manipulation endpoints.

## Features

- **MRPeasy API Integration**: Seamless connection to MRPeasy REST API
- **Data Fetching**: Get customer orders, stock items, manufacturing orders, vendors, and more
- **Data Manipulation**: Create, update, and transform data as needed
- **Reports**: Access production, CRM, and inventory reports
- **CORS Enabled**: Ready for frontend integration
- **Error Handling**: Comprehensive error management

## Setup

1. **Install dependencies**:
   ```bash
   npm install
   ```

2. **Configure environment variables**:
   - Copy `.env.example` to `.env`
   - Add your MRPeasy API credentials:
     ```
     MRPEASY_API_KEY=your_api_key
     MRPEASY_API_SECRET=your_api_secret
     ```

3. **Start the server**:
   ```bash
   npm run dev
   ```

The server will run on `http://localhost:5000`

## API Endpoints

### Customer Orders
- `GET /api/customer-orders` - Get all customer orders
- `GET /api/customer-orders/:id` - Get a specific order
- `POST /api/customer-orders` - Create a new order
- `PUT /api/customer-orders/:id` - Update an order

### Stock Items
- `GET /api/stock-items` - Get all stock items
- `GET /api/stock-items/:id` - Get a specific item

### Manufacturing Orders
- `GET /api/manufacturing-orders` - Get all manufacturing orders
- `GET /api/manufacturing-orders/:id` - Get a specific order

### Vendors
- `GET /api/vendors` - Get all vendors

### Inventory
- `GET /api/inventory` - Get inventory data

### Reports
- `GET /api/report/:reportType` - Get specific report

## Project Structure

```
backend/
├── src/
│   ├── config/          # Configuration files
│   ├── services/        # MRPeasy API service
│   ├── controllers/     # Request handlers
│   ├── routes/          # API routes
│   └── index.js         # Main server file
├── package.json
└── .env.example
```
