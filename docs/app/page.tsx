import Link from 'next/link';
import { ArrowRight, BookOpen, Terminal, Upload } from 'lucide-react';
import { HomeLayout } from 'fumadocs-ui/layouts/home';
import { baseOptions } from '@/lib/layout.shared';

const guides = [
  { icon: Terminal, label: '01 / SET UP', title: 'Run it locally', description: 'Install the Python engine, start the dashboard, and try the example project.', href: '/docs/getting-started/installation' },
  { icon: BookOpen, label: '02 / USE', title: 'Improve your tests', description: 'Start a run, inspect surviving mutants, and review the tests Regress keeps.', href: '/docs/using' },
  { icon: Upload, label: '03 / DEPLOY', title: 'Make it available', description: 'Deploy the application with Docker and Coolify, or host these docs as a static site.', href: '/docs/deployment/application' },
];

export default function Home() {
  return (
    <HomeLayout {...baseOptions}>
      <main className="mx-auto w-full max-w-6xl px-6 py-14 sm:py-24">
        <div className="grid items-center gap-12 lg:grid-cols-[1.25fr_1fr]">
          <div>
            <p className="mb-5 font-mono text-xs uppercase tracking-[0.2em] text-fd-muted-foreground">Regress / Documentation</p>
            <h1 className="max-w-2xl text-4xl leading-[1.1] font-semibold tracking-tight sm:text-6xl">Better tests.<br />Measured by mutation.</h1>
            <p className="mt-6 max-w-lg text-lg leading-relaxed text-fd-muted-foreground">Generate regression tests, find what they miss, and improve them with concrete feedback from Stryker.</p>
            <div className="mt-8 flex flex-wrap items-center gap-4">
              <Link href="/docs/getting-started/installation" className="inline-flex items-center gap-3 rounded-full bg-fd-primary px-5 py-3 text-sm font-medium text-fd-primary-foreground transition-opacity hover:opacity-85">Get started <ArrowRight className="size-4" /></Link>
              <Link href="/docs" className="rounded-full border border-fd-border px-5 py-3 text-sm font-medium transition-colors hover:bg-fd-accent">Read the overview</Link>
            </div>
          </div>
          <div className="overflow-hidden rounded-lg border border-fd-border bg-fd-card">
            <div className="border-b border-fd-border px-5 py-3 font-mono text-xs text-fd-muted-foreground">YOUR PROJECT / TERMINAL</div>
            <pre className="overflow-x-auto px-5 py-6 font-mono text-sm leading-8"><code><span className="text-fd-muted-foreground"># Prepare Vitest and Stryker</span>{'\n'}regress init{'\n\n'}<span className="text-fd-muted-foreground"># Generate, measure, improve</span>{'\n'}regress run src/cart.ts --baseline{'\n\n'}<span className="text-fd-muted-foreground"># Inspect the result</span>{'\n'}regress report</code></pre>
            <div className="border-t border-fd-border px-5 py-3 text-xs text-fd-muted-foreground">Python engine · TypeScript / JavaScript projects</div>
          </div>
        </div>
        <div className="mt-16 grid gap-4 md:grid-cols-3">
          {guides.map(({ icon: Icon, label, title, description, href }) => (
            <Link key={href} href={href} className="group rounded-lg border border-fd-border p-6 transition-colors hover:bg-fd-accent/50">
              <div className="mb-6 flex items-center justify-between text-fd-muted-foreground"><span className="font-mono text-[11px] tracking-wider">{label}</span><Icon className="size-4" /></div>
              <h2 className="mb-2 flex items-center justify-between text-lg font-semibold tracking-tight">{title}<ArrowRight className="size-4 transition-transform group-hover:translate-x-1" /></h2>
              <p className="text-sm leading-relaxed text-fd-muted-foreground">{description}</p>
            </Link>
          ))}
        </div>
        <footer className="mt-12 flex flex-wrap justify-between gap-4 border-t border-fd-border pt-6 text-xs text-fd-muted-foreground">
          <span>One source file. One test file. A measurable feedback loop.</span>
          <Link href="/docs/reference/http-api" className="hover:text-fd-foreground">HTTP API reference →</Link>
        </footer>
      </main>
    </HomeLayout>
  );
}
