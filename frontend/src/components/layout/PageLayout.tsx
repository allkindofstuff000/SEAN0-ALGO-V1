import { ReactNode } from "react";
import { TopNav } from "./TopNav";

interface PageLayoutProps {
  children: ReactNode;
}

export function PageLayout({ children }: PageLayoutProps) {
  return (
    <div className="h-screen bg-background text-foreground flex flex-col font-sans overflow-hidden">
      <TopNav />
      <main className="flex-1 flex flex-col min-h-0 overflow-hidden">
        {children}
      </main>
    </div>
  );
}
