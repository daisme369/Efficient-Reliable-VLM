import type { Metadata } from 'next';
import { Providers } from '@/components/shell';
import './globals.css';

export const metadata: Metadata = {
  title: { default: 'Thorax AI', template: 'Thorax AI — %s' },
  description: 'Academic chest X-ray reading workspace.',
  robots: { index: false, follow: false },
};

export const dynamic = 'force-dynamic';

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body><Providers>{children}</Providers></body></html>;
}
