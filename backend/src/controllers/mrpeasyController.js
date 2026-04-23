const mrpeasyService = require('../services/MRPeasyService');

// Customer Orders Controllers
exports.getCustomerOrders = async (req, res) => {
  try {
    const orders = await mrpeasyService.getCustomerOrders(req.query);
    res.json({ success: true, data: orders });
  } catch (error) {
    res.status(500).json({ success: false, error: error.message });
  }
};

exports.getCustomerOrderById = async (req, res) => {
  try {
    const order = await mrpeasyService.getCustomerOrderById(req.params.id);
    res.json({ success: true, data: order });
  } catch (error) {
    res.status(500).json({ success: false, error: error.message });
  }
};

exports.createCustomerOrder = async (req, res) => {
  try {
    const newOrder = await mrpeasyService.createCustomerOrder(req.body);
    res.status(201).json({ success: true, data: newOrder });
  } catch (error) {
    res.status(500).json({ success: false, error: error.message });
  }
};

exports.updateCustomerOrder = async (req, res) => {
  try {
    await mrpeasyService.updateCustomerOrder(req.params.id, req.body);
    res.json({ success: true, message: 'Order updated successfully' });
  } catch (error) {
    res.status(500).json({ success: false, error: error.message });
  }
};

// Stock Items Controllers
exports.getStockItems = async (req, res) => {
  try {
    const items = await mrpeasyService.getStockItems(req.query);
    res.json({ success: true, data: items });
  } catch (error) {
    res.status(500).json({ success: false, error: error.message });
  }
};

exports.getStockItemById = async (req, res) => {
  try {
    const item = await mrpeasyService.getStockItemById(req.params.id);
    res.json({ success: true, data: item });
  } catch (error) {
    res.status(500).json({ success: false, error: error.message });
  }
};

// Manufacturing Orders Controllers
exports.getManufacturingOrders = async (req, res) => {
  try {
    const orders = await mrpeasyService.getManufacturingOrders(req.query);
    res.json({ success: true, data: orders });
  } catch (error) {
    res.status(500).json({ success: false, error: error.message });
  }
};

exports.getManufacturingOrderById = async (req, res) => {
  try {
    const order = await mrpeasyService.getManufacturingOrderById(req.params.id);
    res.json({ success: true, data: order });
  } catch (error) {
    res.status(500).json({ success: false, error: error.message });
  }
};

// Vendor Controllers
exports.getVendors = async (req, res) => {
  try {
    const vendors = await mrpeasyService.getVendors(req.query);
    res.json({ success: true, data: vendors });
  } catch (error) {
    res.status(500).json({ success: false, error: error.message });
  }
};

// Inventory Controllers
exports.getInventory = async (req, res) => {
  try {
    const inventory = await mrpeasyService.getInventory(req.query);
    res.json({ success: true, data: inventory });
  } catch (error) {
    res.status(500).json({ success: false, error: error.message });
  }
};

// Reports Controllers
exports.getReport = async (req, res) => {
  try {
    const { reportType } = req.params;
    const report = await mrpeasyService.getReport(reportType, req.query);
    res.json({ success: true, data: report });
  } catch (error) {
    res.status(500).json({ success: false, error: error.message });
  }
};
