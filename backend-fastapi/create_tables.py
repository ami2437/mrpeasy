"""
Script to create database tables for shipment_boxes and labels
"""
from app.config.database import engine
from app.models import Base, ShipmentBox, Label

# Create all tables
Base.metadata.create_all(bind=engine)

print("✓ ShipmentBox table created")
print("✓ Label table created")
print("All tables created successfully!")
