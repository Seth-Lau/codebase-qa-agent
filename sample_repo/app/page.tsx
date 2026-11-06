import { getProducts } from "@/lib/db";
import { formatPrice } from "@/lib/format";

export default async function HomePage() {
  const products = await getProducts();
  return (
    <main>
      <h1>Pantry</h1>
      <ul>
        {products.map((p) => (
          <li key={p.id}>
            <a href={`/products/${p.id}`}>{p.name}</a> - {formatPrice(p.price_pence)}
          </li>
        ))}
      </ul>
    </main>
  );
}
