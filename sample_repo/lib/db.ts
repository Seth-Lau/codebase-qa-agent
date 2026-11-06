import { createClient } from "@supabase/supabase-js";

// The database client. Keys come from environment variables, never from code.
const supabase = createClient(
  process.env.SUPABASE_URL!,
  process.env.SUPABASE_ANON_KEY!,
);

export type Product = { id: string; name: string; price_pence: number };

export async function getProducts(): Promise<Product[]> {
  const { data, error } = await supabase.from("products").select("id, name, price_pence");
  if (error) throw error;
  return data;
}

export async function getProduct(id: string): Promise<Product | null> {
  const { data } = await supabase.from("products").select("*").eq("id", id).maybeSingle();
  return data;
}

export async function listOrders(limit: number) {
  const { data, error } = await supabase
    .from("orders")
    .select("*")
    .order("created_at", { ascending: false })
    .limit(limit);
  if (error) throw error;
  return data;
}

export async function createOrder(order: {
  customer_name: string;
  product_id: string;
  quantity: number;
  total_pence: number;
}) {
  const { data, error } = await supabase.from("orders").insert(order).select().single();
  if (error) throw error;
  return data;
}
