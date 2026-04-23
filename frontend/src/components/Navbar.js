import React from 'react';
import './Navbar.css';

function Navbar() {
  return (
    <nav className="navbar">
      <div className="navbar-container">
        <div className="navbar-brand">
          <h1>MRPeasy Custom Portal</h1>
        </div>
        <ul className="navbar-menu">
          <li><a href="/">Dashboard</a></li>
          <li><a href="/orders">Orders</a></li>
          <li><a href="/inventory">Inventory</a></li>
          <li><a href="/manufacturing">Manufacturing</a></li>
          <li><a href="/vendors">Vendors</a></li>
        </ul>
      </div>
    </nav>
  );
}

export default Navbar;
