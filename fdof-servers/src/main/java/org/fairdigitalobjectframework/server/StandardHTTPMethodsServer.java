package org.fairdigitalobjectframework.server;

import io.javalin.Javalin;
import io.javalin.http.Context;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;

public class StandardHTTPMethodsServer {

    public static String getFileExtension(Path path) {
        if(!Files.isRegularFile(path)) {
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
    public static void readResource(Context ctx, String resourceType) {
        var id = ctx.pathParam("id");

        List<Path> pathList = new ArrayList<>();

        try {
            pathList = Files.list(Paths.get("src/main/resources/"+resourceType+"/"))
                    .filter(Files::isRegularFile)
                    .filter(path -> path.getFileName().toString().startsWith(id))
                    .toList();
        } catch (IOException e) {
            e.printStackTrace();
            ctx.result("Unable to read "+resourceType+" '"+id+"'.");
            ctx.res().setStatus(500);
            return;
        }

        if(pathList.isEmpty()){
            ctx.result("The "+resourceType+" '"+id+"' does not exist.");
            ctx.res().setStatus(404);
            return;
        }

        try {
            var path = pathList.get(0);
            String content = Files.readString(path);
            ctx.result(content);
            ctx.res().setStatus(200);
            ctx.res().addHeader("Content-Type", getConteType(path));
        }
        catch (IOException ex) {
            ex.printStackTrace();
            ctx.result("Unable to read resource '"+id+"'");
            ctx.res().setStatus(500);
        }
    }

    public static void main(String[] args) {

        var app = Javalin.create(/*config*/)
                .get("/{id}", ctx -> readResource(ctx, "digital object"))
                .get("/{id}/identifierRecord", ctx -> readResource(ctx, "identity record"))
                .get("/{id}/metadataRecord", ctx -> readResource(ctx, "metadata record"))
                .get("/{id}/type", ctx -> readResource(ctx, "type record"))
                .start(7070);

    }
}
