-- Customer account table
CREATE TABLE Customer (
    CustomerID    INT          NOT NULL PRIMARY KEY,
    Email         VARCHAR(255) NOT NULL UNIQUE,
    Name          VARCHAR(100) NOT NULL,
    CreatedAt     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    IsActive      BIT          NOT NULL DEFAULT 1
);

-- Orders placed by customers
CREATE TABLE Order_ (
    OrderID     INT           NOT NULL PRIMARY KEY,
    CustomerID  INT           NOT NULL,
    Status      VARCHAR(20)   NOT NULL,
    PlacedAt    DATETIME      NOT NULL,
    FOREIGN KEY (CustomerID) REFERENCES Customer(CustomerID)
);

CREATE INDEX idx_order_customer ON Order_ (CustomerID);

CREATE VIEW ActiveCustomers AS
    SELECT CustomerID, Email, Name
    FROM Customer
    WHERE IsActive = 1;
