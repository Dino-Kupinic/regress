import type { BaseLayoutProps } from 'fumadocs-ui/layouts/shared';
import { Logo } from '@/components/logo';

export const baseOptions: BaseLayoutProps = {
  nav: { title: <Logo /> },
  githubUrl: 'https://github.com/Dino-Kupinic/regress',
  links: [
    { text: 'Documentation', url: '/docs', active: 'nested-url' },
    { text: 'Deployment', url: '/docs/deployment/application', active: 'url' },
  ],
};
