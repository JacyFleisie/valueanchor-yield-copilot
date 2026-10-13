import './styles.css'
import type { Metadata } from 'next'

export const metadata: Metadata = { title: 'ValueAnchor — Yield Copilot', description: 'Protect the price floor. Sell the stock before it lapses.' }
export default function RootLayout({ children }: { children: React.ReactNode }) { return <html lang="en"><body>{children}</body></html> }
