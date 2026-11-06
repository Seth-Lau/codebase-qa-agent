create table products (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  price_pence integer not null check (price_pence >= 0)
);

create table orders (
  id uuid primary key default gen_random_uuid(),
  customer_name text not null,
  product_id uuid not null references products(id),
  quantity integer not null check (quantity > 0),
  total_pence integer not null,
  created_at timestamptz not null default now()
);
