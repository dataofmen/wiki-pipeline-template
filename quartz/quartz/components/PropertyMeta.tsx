import { QuartzComponent, QuartzComponentConstructor, QuartzComponentProps } from "./types"
import { classNames } from "../util/lang"
import { i18n } from "../i18n"
import { JSX } from "preact"

const PropertyMeta: QuartzComponent = ({ fileData, displayClass }: QuartzComponentProps) => {
  const fm = fileData.frontmatter
  if (!fm) return null

  const items: JSX.Element[] = []

  // type (entity, concept, atomic, etc.)
  if (fm.type) {
    items.push(
      <span class="property-item">
        <span class="property-label">유형</span>
        <span class="property-value">{fm.type as string}</span>
      </span>,
    )
  }

  // space (B_arscontexta)
  if (fm.space) {
    items.push(
      <span class="property-item">
        <span class="property-label">공간</span>
        <span class="property-value">{fm.space as string}</span>
      </span>,
    )
  }

  // confidence (A_wiki)
  if (fm.confidence) {
    items.push(
      <span class="property-item">
        <span class="property-label">신뢰도</span>
        <span class="property-value">{fm.confidence as string}</span>
      </span>,
    )
  }

  // status (B_arscontexta)
  if (fm.status) {
    items.push(
      <span class="property-item">
        <span class="property-label">상태</span>
        <span class="property-value">{fm.status as string}</span>
      </span>,
    )
  }

  if (items.length === 0) return null

  return (
    <div class={classNames(displayClass, "property-meta")}>
      {items}
    </div>
  )
}

PropertyMeta.css = `
.property-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
  margin: 0.5rem 0 1rem 0;
  font-size: 0.85rem;
  color: var(--gray);
}

.property-item {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  background: var(--lightgray);
  padding: 0.2rem 0.5rem;
  border-radius: 4px;
}

.property-label {
  font-weight: 600;
  color: var(--darkgray);
  text-transform: uppercase;
  letter-spacing: 0.02em;
  font-size: 0.75rem;
}

.property-value {
  color: var(--secondary);
}
`

export default (() => PropertyMeta) satisfies QuartzComponentConstructor
