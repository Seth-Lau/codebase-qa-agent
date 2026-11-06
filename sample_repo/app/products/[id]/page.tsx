import { getProduct } from "@/lib/db";
import { formatPrice } from "@/lib/format";

export default async function ProductPage({ params }: { params: { id: string } }) {
  const product = await getProduct(params.id);
  if (!product) {
    return <p>Product not found.</p>;
  }
  return (
    <main>
      <h1>{product.name}</h1>
      <p>{formatPrice(product.price_pence)}</p>
    </main>
  );
}
