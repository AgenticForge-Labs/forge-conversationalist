import type { Metadata } from "next";
import "@livekit/components-styles";
import "./styles.css";

export const metadata: Metadata = {
  title: "Forge Conversationalist",
  description: "A standalone, LiveKit-powered voice conversation interface",
};

export default function Layout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
