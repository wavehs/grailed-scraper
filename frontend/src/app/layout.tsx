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
          <div className="flex min-h-dvh">
            <AppSidebar />
            <main id="main-content" className="min-w-0 flex-1 px-4 pb-8 pt-16 md:px-6 md:py-6 xl:px-8">
              <div className="mx-auto max-w-[1480px]">
                <HealthBanner />
                {children}
              </div>
            </main>
          </div>
        </Providers>
      </body>
    </html>
  );
}
