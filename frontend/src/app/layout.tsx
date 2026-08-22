import type { Metadata } from 'next';
import { AppSidebar } from '@/components/app-sidebar';
import { Providers } from '@/components/providers';
import { HealthBanner } from '@/components/health-banner';
import './globals.css';
export const metadata: Metadata = {
  title: 'Grailed Liquidity Analyzer',
  description: 'Local Grailed market demand intelligence.',
};
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <Providers>
          <div className="flex min-h-screen">
            <AppSidebar />
            <main className="min-w-0 flex-1 p-4 pt-16 md:p-8 md:pt-8">
              <HealthBanner />
              <div className="mx-auto max-w-7xl">{children}</div>
            </main>
          </div>
        </Providers>
      </body>
    </html>
  );
}
