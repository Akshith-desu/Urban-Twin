import type { Metadata } from "next";
import "./globals.css";
import Sidebar from "./components/Sidebar";

export const metadata: Metadata = {
  title: "Urban Twin Simulation",
  description: "Real-time infrastructure cascade simulation",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <head>
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
           integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
           crossOrigin=""/>
      </head>
      <body className="antialiased text-foreground bg-background h-screen flex">
        <Sidebar />
        <main className="flex-1 relative h-full flex flex-col">
          {children}
        </main>
      </body>
    </html>
  );
}
