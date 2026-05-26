package org.fairdigitalobjectframework.server;

import io.javalin.Javalin;
import io.javalin.http.Context;
import io.javalin.http.InternalServerErrorResponse;
import io.javalin.http.NotFoundResponse;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.List;
import java.util.Optional;

public class SignpostingServer {
    public static String getFileExtension(Path path) {
        if (!Files.isRegularFile(path)) {
            throw new RuntimeException("Unable to get file extension. Path does not point to a file.");
        }

        String fileName = path.toString();
        // Find the last occurrence of '.' in the filename
        int dotIndex = fileName.lastIndexOf('.');
        // If '.' is not found, return "No extension", otherwise return the substring after '.'
        return (dotIndex == -1) ? "No extension" : fileName.substring(dotIndex + 1);
    }

    public static String getConteType(Path path) {
        var extension = getFileExtension(path);

        switch (extension) {
            case "ttl":
                return "text/turtle";
            case "csv":
                return "text/csv";
            case "json":
                return "application/json";
            case "trig":
                return "application/trig";
            default:
                return "text/plain";
        }
    }

    public static void resourceExists(String id) {

    }

    public static Optional<Path> findResourceFile(String resourceType, String id) {
        List<Path> pathList;

        try {
            pathList = Files.list(Paths.get("src/main/resources/" + resourceType + "/"))
                    .filter(Files::isRegularFile)
                    .filter(path -> path.getFileName().toString().startsWith(id+"."))
                    .toList();
        } catch (IOException e) {

            e.printStackTrace();
            return Optional.empty();
        }

        if (pathList.isEmpty()) {
            return Optional.empty();
        }

        return Optional.ofNullable(pathList.get(0));
    }

    public static void getDigitalObjectRelationship(Context ctx, String resourceType, boolean includeBody) {
        validateDigitalObjectExists(ctx);

        var id = ctx.pathParam("id");
        var path = findResourceFile(resourceType, id).orElseThrow(() -> new NotFoundResponse("The " + resourceType + " of digital object '" + id + "' does not exist."));

        try {
            if (includeBody) {
                String content = Files.readString(path);
                ctx.result(content);
            }

            ctx.res().setStatus(200);
            ctx.res().addHeader("Content-Type", getConteType(path));
        } catch (IOException ex) {
            ex.printStackTrace();
            throw new InternalServerErrorResponse("Unable to read "+resourceType+" of digital object'" + id + "'");
        }
    }

    public static void getDigitalObject(Context ctx, boolean includeBody) {
        validateDigitalObjectExists(ctx);

        var id = ctx.pathParam("id");
        var path = findResourceFile("digital object", id).orElseThrow(NotFoundResponse::new);

        try {
            String content = Files.readString(path);

            if (includeBody) {
                ctx.result(content);
            }
            ctx.header("Content-Length", String.valueOf(content.getBytes(StandardCharsets.UTF_8).length));

            ctx.res().setStatus(200);
            ctx.res().addHeader("Content-Type", getConteType(path));
            ctx.res().addHeader("Link", "<http://localhost:7070/" + id + "/identity>; rel=\"fdof-ir\"");
            ctx.res().addHeader("Link", "<http://localhost:7070/" + id + "/metadata>; rel=\"fdof-metadata\"");
            ctx.res().addHeader("Link", "<http://localhost:7070/" + id + "/type>; rel=\"fdof-type\"");

        } catch (IOException ex) {
            ex.printStackTrace();
            throw new InternalServerErrorResponse("Unable to read resource '" + id + "'");
        }
    }

    public static void validateDigitalObjectExists(Context ctx) {
        var id = ctx.pathParam("id");
        var path = findResourceFile("digital object", id);

        if (path.isEmpty()) {
            throw new NotFoundResponse("The digital object '" + id + "' does not exist.");
        }
    }

    public static void main(String[] args) {

        var app = Javalin.create(/*config*/)
                .head("/{id}", ctx -> getDigitalObject(ctx, false))
                .get("/{id}", ctx -> getDigitalObject(ctx, true))
                .get("/{id}/identifierRecord", ctx -> getDigitalObjectRelationship(ctx, "identity record", true))
                .head("/{id}/identifierRecord", ctx -> getDigitalObjectRelationship(ctx, "identity record", false))
                .get("/{id}/metadataRecord", ctx -> getDigitalObjectRelationship(ctx, "metadata record", true))
                .head("/{id}/metadataRecord", ctx -> getDigitalObjectRelationship(ctx, "metadata record", false))
                .get("/{id}/type", ctx -> getDigitalObjectRelationship(ctx, "type record", true))
                .head("/{id}/type", ctx -> getDigitalObjectRelationship(ctx, "type record", false))
                .start(7070);

    }
}
