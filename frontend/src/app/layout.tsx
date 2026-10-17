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
            {/* PageHeader bleeds over this gutter to form the 44px top bar of every screen. */}
            <main id="main-content" className="min-w-0 flex-1 px-4 pb-10 pt-[60px] md:px-6 md:pt-4">
              <HealthBanner />
              {children}
            </main>
          </div>
        </Providers>
      </body>
    </html>
  );
}
