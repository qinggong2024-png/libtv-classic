import { AuthenticatedImage } from './Media'

export function VideoThumbnail({ artifactId, alt }: { artifactId: string; alt: string }) {
  return <AuthenticatedImage key={artifactId} src={`/api/v1/downloads/${artifactId}/thumbnail`} alt={alt} />
}
