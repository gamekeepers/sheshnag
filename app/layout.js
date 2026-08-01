import { Geist, Geist_Mono } from "next/font/google";
import GoogleAuthProviderWrapper from './components/GoogleAuthProviderWrapper';
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata = {
  title: "MOONKNIGHT — AI Batch Processing",
  description: "Process thousands of prompts in one shot.",
};

export default function RootLayout({ children }) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <GoogleAuthProviderWrapper>
          {children}
        </GoogleAuthProviderWrapper>
      </body>
    </html>
  );
}
