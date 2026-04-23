const express = require('express');
const router = express.Router();
const controller = require('../controllers/mrpeasyController');

// Customer Orders Routes
router.get('/customer-orders', controller.getCustomerOrders);
router.get('/customer-orders/:id', controller.getCustomerOrderById);
router.post('/customer-orders', controller.createCustomerOrder);
router.put('/customer-orders/:id', controller.updateCustomerOrder);

// Stock Items Routes
router.get('/stock-items', controller.getStockItems);
router.get('/stock-items/:id', controller.getStockItemById);

// Manufacturing Orders Routes
router.get('/manufacturing-orders', controller.getManufacturingOrders);
router.get('/manufacturing-orders/:id', controller.getManufacturingOrderById);

// Vendors Routes
router.get('/vendors', controller.getVendors);

// Inventory Routes
router.get('/inventory', controller.getInventory);

// Reports Routes
router.get('/report/:reportType', controller.getReport);

module.exports = router;
