import { NextResponse } from "next/server";
import { createOrder, listOrders } from "@/lib/db";
import { calculateTotal } from "@/lib/pricing";

// GET /api/orders - the 20 most recent orders
export async function GET() {
  const orders = await listOrders(20);
  return NextResponse.json(orders);
}

// POST /api/orders - create an order
export async function POST(request: Request) {
  const body = await request.json();

  if (!body.customerName || !body.productId) {
    return NextResponse.json({ error: "customerName and productId are required" }, { status: 400 });
  }
  if (!Number.isInteger(body.quantity) || body.quantity < 1) {
    return NextResponse.json({ error: "quantity must be a whole number of at least 1" }, { status: 400 });
  }

  const totalPence = calculateTotal(body.unitPricePence, body.quantity);
  const order = await createOrder({
    customer_name: body.customerName,
    product_id: body.productId,
    quantity: body.quantity,
    total_pence: totalPence,
  });
  return NextResponse.json(order, { status: 201 });
}
