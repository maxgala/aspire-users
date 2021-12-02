import logging
import boto3
import requests
import io
from botocore.exceptions import ClientError
from requests import RequestException
from PIL import Image
from io import BytesIO

from user import User
from base import Session

logger = logging.getLogger()
logger.setLevel(logging.INFO)

cognito_client = boto3.client('cognito-idp')
s3_client = boto3.client('s3')
ses_client = boto3.client('ses')

ADMIN_EMAIL = "aspire@maxgala.com"
SUPPORT_EMAIL = "aspire@maxgala.com"

CHARSET = "UTF-8"
SUBJECT = "Welcome to MAX Aspire!"


def send_email(source_email, to_addresses, subject, body_text, body_html, charset):
    try:
        response = ses_client.send_email(
            Destination={
                'ToAddresses': to_addresses,
            },
            Message={
                'Body': {
                    'Text': {
                        'Charset': charset,
                        'Data': body_text,
                    },
                },
                'Subject': {
                    'Charset': charset,
                    'Data': subject,
                },
            },
            Source=source_email,
        )
    except ClientError as e:
        logger.info(e.response['Error']['Message'])
    else:
        logger.info("Email sent! Message ID:"),
        logger.info(response['MessageId'])


def sync_with_db(username, user_type, industry, industry_tags, first_name, last_name, status):

    session = Session()
    user = User(username=username, user_type=user_type,
                industry=industry, first_name=first_name, last_name=last_name, industry_tags=industry_tags, status=status)
    session.add(user)
    session.commit()
    session.close()

def compress_image_obj(image_obj, quality, bucket_name, object_name):
    output_path = io.BytesIO()
    image_obj.save(output_path, image_obj.format)
    original_file_size = output_path.tell()

    output_path = io.BytesIO()
    if image_obj.mode in ("RGBA", "P"): image_obj = image_obj.convert("RGB")
    image_obj.save(output_path, 'JPEG', quality=quality)
    new_file_size = output_path.tell()

    if new_file_size < original_file_size:
        output_path.seek(0)
        s3_client.upload_fileobj(output_path, bucket_name, object_name, ExtraArgs = {"ContentType": 'image/jpeg', 'ACL': 'public-read'})
        return True
    else:
        return False

class UnaccessibleUrlException(Exception):
    pass

def compress_user_image(user_email, user_pictureURL, userPoolId, quality=25):
    try:
        #Get user picture url
        response = requests.get(user_pictureURL)
        if not response.ok:
            raise UnaccessibleUrlException
    except (UnaccessibleUrlException, RequestException):
        print("Broken Url")
    else:
        if (user_pictureURL.find("blank_profile.png")) < 0:
            try:
                image_obj = Image.open(BytesIO(response.content))
                filename_index = user_pictureURL.find('pictures/')
                extension_index = user_pictureURL.rfind('.')
                filename = user_pictureURL[filename_index + 9: extension_index] + '-CompressedTest2.jpg'
                S3_object_name = user_email + '/pictures/' + filename
                bucket_name = 'aspire-user-profile'
                image_compressed = compress_image_obj(image_obj, quality, bucket_name, S3_object_name)

                #Edit cognito reference for picture URL
                if image_compressed == True:
                        new_image_url = 'https://aspire-user-profile.s3.amazonaws.com/' + user_email + '/pictures/' + filename
                        response = cognito_client.admin_update_user_attributes(
                            UserPoolId=userPoolId,
                            Username=user_email,
                            UserAttributes=[
                                {'Name': 'picture', 'Value': new_image_url} 
                            ]
                        )
            except Exception as exc:
                print("Exception")
    return True


def handler(event, context):
    logger.info(event)
    logger.info(context)
    if event['triggerSource'] == 'PostConfirmation_ConfirmForgotPassword':
        return event

    user_type = event['request']['userAttributes'].get('custom:user_type', '')
    user_email = event['request']['userAttributes']['email']
    user_fname = event['request']['userAttributes']['given_name']
    user_lname = event['request']['userAttributes']['family_name']
    user_pictureURL = event['request']['userAttributes']['picture']
    user_industry = event['request']['userAttributes'].get(
        'custom:industry', '')
    user_industry_tags = event['request']['userAttributes'].get(
        'custom:industry_tags', '')

    logger.info('confirming user {%s} with user_type {%s}' % (
        user_email, user_type))
    if user_type == 'ADMIN':
        # TODO: send email
        logger.info('disabling user of type {%s}' % (user_type))
        response = cognito_client.admin_disable_user(
            UserPoolId=event['userPoolId'],
            Username=event['userName']
        )
        logger.info(response)
    elif user_type == 'FREE':
        sync_with_db(user_email, user_type, user_industry, user_industry_tags,
                     user_fname, user_lname, 'ENABLED')
        #Compress user image
        userPoolId=event['userPoolId']
        compress_user_image(user_email, user_pictureURL, userPoolId)

        #Send email
        BODY_TEXT = (f"Salaam {user_fname}!\r\n"
                     "\r\n\n"
                     "Congratulations for successfully signing up on MAX Aspire! We are thrilled to have you on board and can’t wait to make a positive difference in your professional career."
                     "At MAX, we are devoted to elevating the Muslim brand by serving aspiring professionals, such as yourself! The Aspire platform aims to bring together a powerful network to collaborate for a more rewarding career journey and help Muslims fulfill their true potential. More than 200 Senior Executives, including CEOs, Partners, Managing Directors and VPs, are already on board! You are now a part of this circle too!\r\n"
                     "\r\n\n"
                     "Check out the cool features we currently offer:\r\n"
                     "1. Resume Bank\r\n"
                     "2. Exclusive Coffee Chats\r\n"
                     "3. Hire MAX Professional Talent\r\n"
                     "4. Mock Interviews\r\n"
                     "\r\n\n"
                     "We sincerely hope you make the most of these services and help spread the word. As the Prophet said: 'Every Act of goodness is charity.' (Sahih Muslim, Hadith 496)\r\n"
                     "You can now access your account at https://aspire.maxgala.com\r\n"
                     "Should you need any assistance or have any questions or comments about your membership or benefits, please feel free to contact us at aspire@maxgala.com\r\n"
                     "\r\n\n"
                     "Sincerely,\r\n"
                     "Aazar Zafar\r\n"
                     "Founder and Head Cheerleader\r\n"
                     "MAX Aspire"
                     )
        send_email(ADMIN_EMAIL, [user_email],
                   SUBJECT, BODY_TEXT, None, CHARSET)
    elif user_type == 'PAID':
        sync_with_db(user_email, user_type, user_industry, user_industry_tags,
                     user_fname, user_lname, 'ENABLED')
        #Compress user image
        userPoolId=event['userPoolId']
        compress_user_image(user_email, user_pictureURL, userPoolId)

        #Send email
        BODY_TEXT = (f"Salaam {user_fname}!\r\n"
                     "\r\n\n"
                     "Congratulations for successfully signing up on MAX Aspire! We are thrilled to have you on board and can’t wait to make a positive difference in your professional career."
                     "At MAX, we are devoted to elevating the Muslim brand by serving aspiring professionals, such as yourself! The Aspire platform aims to bring together a powerful network to collaborate for a more rewarding career journey and help Muslims fulfill their true potential. More than 200 Senior Executives, including CEOs, Partners, Managing Directors and VPs, are already on board! You are now a part of this circle too!\r\n"
                     "\r\n\n"
                     "Check out the cool features we currently offer:\r\n"
                     "1. Resume Bank\r\n"
                     "2. Exclusive Coffee Chats\r\n"
                     "3. Hire MAX Professional Talent\r\n"
                     "4. Mock Interviews\r\n"
                     "\r\n\n"
                     "We sincerely hope you make the most of these services and help spread the word. As the Prophet said: 'Every Act of goodness is charity.' (Sahih Muslim, Hadith 496)\r\n"
                     "You can now access your account at https://aspire.maxgala.com\r\n"
                     "Should you need any assistance or have any questions or comments about your membership or benefits, please feel free to contact us at aspire@maxgala.com\r\n"
                     "\r\n\n"
                     "Sincerely,\r\n"
                     "Aazar Zafar\r\n"
                     "Founder and Head Cheerleader\r\n"
                     "MAX Aspire"
                     )
        send_email(ADMIN_EMAIL, [user_email],
                   SUBJECT, BODY_TEXT, None, CHARSET)
    elif user_type == 'MENTOR':
        logger.info('disabling user of type {%s}' % (user_type))
        response = cognito_client.admin_disable_user(
            UserPoolId=event['userPoolId'],
            Username=event['userName']
        )
        logger.info(response)

        sync_with_db(user_email, user_type, user_industry, user_industry_tags,
                     user_fname, user_lname, 'DISABLED')
        #Compress user image
        userPoolId=event['userPoolId']
        compress_user_image(user_email, user_pictureURL, userPoolId)

        #Send email
        BODY_TEXT = (f"Salaam {user_fname}!\r\n"
                     "\r\n\n"
                     "Thank you for signing up as a Senior Executive on MAX Aspire. "
                     "Our team is working on your request and will send over an update within 48 to 72 hours.\r\n"
                     "We really appreciate your time and commitment to helping our Aspiring Professionals. "
                     "Please don’t hesitate to reach out to aspire@maxgala.com if you have any questions.\r\n"
                     "\r\n\n"
                     "Best,\r\n"
                     "MAX Aspire Team"
                     )
        send_email(ADMIN_EMAIL, [user_email],
                   SUBJECT, BODY_TEXT, None, CHARSET)
    else:
        logger.error(
            'invalid user_type: disabling user of type {%s}' % (user_type))
        response = cognito_client.admin_disable_user(
            UserPoolId=event['userPoolId'],
            Username=event['userName']
        )
        logger.info(response)

    return event
