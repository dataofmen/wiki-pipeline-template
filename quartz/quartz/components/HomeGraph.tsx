import { QuartzComponent, QuartzComponentConstructor, QuartzComponentProps } from "./types"
// @ts-ignore
import script from "./scripts/graph.inline"
import style from "./styles/graph.scss"
import { i18n } from "../i18n"

export default ((opts?: { height?: string }) => {
  const height = opts?.height ?? "60vh"

  const HomeGraph: QuartzComponent = ({ displayClass, cfg, fileData }: QuartzComponentProps) => {
    // Only render on index page
    if (fileData.slug !== "index") return <></>

    const globalGraph = {
      drag: true,
      zoom: true,
      depth: -1,
      scale: 0.9,
      repelForce: 0.5,
      centerForce: 0.2,
      linkDistance: 30,
      fontSize: 0.6,
      opacityScale: 1,
      showTags: true,
      removeTags: [],
      focusOnHover: true,
      enableRadial: true,
    }

    return (
      <div class="home-graph" style={`height: ${height}; border: 1px solid var(--lightgray); border-radius: 5px; margin: 0.5em 0; overflow: hidden;`}>
        <div class="graph-outer" style="height: 100%; position: relative;">
          <div class="graph-container" style="height: 100%; width: 100%;" data-cfg={JSON.stringify(globalGraph)}></div>
        </div>
      </div>
    )
  }

  HomeGraph.css = style
  HomeGraph.afterDOMLoaded = script

  return HomeGraph
}) satisfies QuartzComponentConstructor