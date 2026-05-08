import { QuartzComponent, QuartzComponentConstructor } from "./types"

interface NavLinksOptions {
  links: { label: string; path: string }[]
}

const NavLinks: QuartzComponent = () => {
  const options: NavLinksOptions = {
    links: [
      { label: "wiki", path: "/wiki" },
      { label: "garden", path: "/garden" },
      { label: "clippings", path: "/clippings" },
    ],
  }

  return (
    <nav class="nav-links">
      {options.links.map(({ label, path }) => (
        <a href={path} class="nav-link">
          {label}
        </a>
      ))}
    </nav>
  )
}

NavLinks.css = `
.nav-links {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.nav-link {
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--darkgray);
  text-decoration: none;
  padding: 0.25rem 0.6rem;
  border-radius: 4px;
  transition: background 0.15s, color 0.15s;
}

.nav-link:hover {
  background: var(--highlight);
  color: var(--dark);
}
`

export default (() => NavLinks) satisfies QuartzComponentConstructor
