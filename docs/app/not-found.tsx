import Link from 'next/link';

export default function NotFound() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 px-6 text-center">
      <p className="font-mono text-sm text-fd-muted-foreground">404</p>
      <h1 className="text-3xl font-semibold">Page not found</h1>
      <p className="text-fd-muted-foreground">This documentation page does not exist.</p>
      <Link href="/docs" className="rounded-full bg-fd-primary px-5 py-2 text-sm font-medium text-fd-primary-foreground">Browse the documentation</Link>
    </main>
  );
}
