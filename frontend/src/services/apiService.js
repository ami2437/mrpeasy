import axios from 'axios';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:5000/api';

const apiService = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Customer Orders
export const getCustomerOrders = (filters = {}) =>
  apiService.get('/customer-orders', { params: filters });

export const getCustomerOrderById = (id) =>
  apiService.get(`/customer-orders/${id}`);

export const createCustomerOrder = (orderData) =>
  apiService.post('/customer-orders', orderData);

export const updateCustomerOrder = (id, updateData) =>
  apiService.put(`/customer-orders/${id}`, updateData);

// Stock Items
export const getStockItems = (filters = {}) =>
  apiService.get('/stock-items', { params: filters });

export const getStockItemById = (id) =>
  apiService.get(`/stock-items/${id}`);

// Manufacturing Orders
export const getManufacturingOrders = (filters = {}) =>
  apiService.get('/manufacturing-orders', { params: filters });

export const getManufacturingOrderById = (id) =>
  apiService.get(`/manufacturing-orders/${id}`);

// Vendors
export const getVendors = (filters = {}) =>
  apiService.get('/vendors', { params: filters });

// Inventory
export const getInventory = (filters = {}) =>
  apiService.get('/inventory', { params: filters });

// Reports
export const getReport = (reportType, filters = {}) =>
  apiService.get(`/report/${reportType}`, { params: filters });

export default apiService;
